# Emerald
# Copyright (C) 2026 Juan José Caballero Rey - https://github.com/rey-sudo
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation version 3 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

import os
import asyncio
import json
import time
import logging
import signal
import threading
import pulsar
import asyncpg
import aioboto3
from botocore.config import Config
from uuid6 import uuid7
from tools import process_pdf
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# CONFIG ---------------------------------------------------------------------------------------------------------------

PULSAR_URL = "pulsar://emerald-broker:6650"
TOPIC = ["persistent://public/default/document.created"]
SUBSCRIPTION_NAME = "document-transform-worker-group-shared"

DATABASE_URL = "postgres://postgres:password@emerald-postgres:5432/document_transform"
S3_BUCKET = "documents"

CONCURRENCY = 40
QUEUE_SIZE = 500

logging.basicConfig(level=logging.INFO)

# GLOBAL ---------------------------------------------------------------------------------------------------------------

queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
semaphore = asyncio.Semaphore(CONCURRENCY)
shutdown_event = asyncio.Event()
stop_listener = threading.Event() #Pulsar listener in Threading 
_active_tasks: set[asyncio.Task] = set()

# S3 CLIENT ------------------------------------------------------------------------------------------------------------

S3_CONFIG = Config(
    retries={"max_attempts": 3, "mode": "standard"},
    max_pool_connections=50, #CONCURRENCY
    connect_timeout=5,
    read_timeout=30,
)

s3_client = None  # GLOBAL

async def init_s3():
    global s3_client

    session = aioboto3.Session()

    s3_client = await session.client(
        "s3",
        endpoint_url=os.environ.get("S3_ENDPOINT"),
        aws_access_key_id=os.environ.get("S3_ACCESS_KEY"),
        aws_secret_access_key=os.environ.get("S3_SECRET_KEY"),
        region_name=os.environ.get("S3_REGION"),
        config=S3_CONFIG,
    ).__aenter__()


async def close_s3():
    global s3_client
    if s3_client:
        await s3_client.__aexit__(None, None, None)

# DATABASE ----------------------------------------------------------------------------------------------------

@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=10),  
    retry=retry_if_exception_type(Exception), 
    reraise=True 
)
async def check_if_consumed(conn, event_id: str) -> bool:
    query = """
        SELECT EXISTS (
            SELECT 1 
            FROM processed_events 
            WHERE event_id = $1
        );
    """
    return await conn.fetchval(query, event_id)


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=10),  
    retry=retry_if_exception_type(Exception), 
    reraise=True 
)
async def insert_processed(conn, event_id: str, ts):
    query = """
        INSERT INTO processed_events (event_id, created_at)
        VALUES ($1,$2)
        ON CONFLICT (event_id) DO NOTHING;
    """
    return await conn.execute(query, event_id, ts)

    
@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=10),  
    retry=retry_if_exception_type(Exception), 
    reraise=True 
)
async def insert_outbox(conn, document, ts, checksum, metadata):
        payload = {
            "id": document.get("id"),
            "status": "PROCESSED",
            "checksum": checksum,
            "metadata": {},
            "v": document.get("v")
        }
        return await conn.execute(
            """
            INSERT INTO events (
                specversion, event_type, source, id, time,
                entity_type, entity_id, data, metadata
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            """,
            0,
            "document.processed",
            "document-transform-worker",
            uuid7(),
            ts,
            "document",
            document.get("id"),
            json.dumps(payload),
            json.dumps(metadata),
        )

# HANDLE ---------------------------------------------------------------------------------------------------------------

async def handle(msg, consumer, pool):
    try:
        payload = json.loads(msg.data())
        event_id = payload.get("event_id")
        
        #TODO: REDIS event_id LOCK ok
        
        event_type = payload.get("event_type")
        document = payload.get("data") or {}
        metadata = payload.get("metadata") or {}

        doc_id = document.get("id")
        doc_mime = document.get("mime_type")

        if not event_id or not doc_id:
            logging.warning("Invalid serialized event.")
            consumer.acknowledge(msg)
            return

        if event_type != "document.created":
            logging.warning("Invalid event_type")
            consumer.acknowledge(msg)
            return

        ts = int(time.time() * 1000)

        # TRANSACTION ----------------------------------------------------------------------------------------------

        async with pool.acquire() as conn:
            #NO INSERTA NADA SOLO VERIFICA IDEMPOTENCIA.
            is_consumed = await check_if_consumed(conn, event_id)
            if is_consumed:
                logging.warning(f"Event already processed: {event_id}")
                consumer.acknowledge(msg)
                return

            match doc_mime:
                case "application/pdf":
                    checksum = await asyncio.wait_for(
                        process_pdf(s3_client, S3_BUCKET, payload),
                        timeout=60
                    )
                case _:
                    logging.warning("Invalid doc_mime")
                    consumer.acknowledge(msg)
                    return #TX FINISH
                            
            async with conn.transaction():
                await insert_processed(conn, event_id, ts)
                await insert_outbox(conn, document, ts, checksum, metadata)
                #TX COMMIT
                
        consumer.acknowledge(msg)
                
        # TRANSACTION END----------------------------------------------------------------------------------------------
        
    except Exception:
        logging.exception("error")
        consumer.negative_acknowledge(msg)

# WORKER LOOP ----------------------------------------------------------------------------------------------------------

async def worker_loop(consumer, pool):
    """
    Orchestrates asynchronous task dispatching by consuming messages from the internal queue 
    and managing their lifecycle through active task tracking.
    """    
    while not shutdown_event.is_set():
        msg = await queue.get()

        await semaphore.acquire()

        async def run(m):
            try:
                await handle(m, consumer, pool)
            finally:
                semaphore.release()

        task = asyncio.create_task(run(msg))
        _active_tasks.add(task)
        task.add_done_callback(_active_tasks.discard)
        queue.task_done()

# PULSAR LISTENER ------------------------------------------------------------------------------------------------------

def listener(loop, consumer):
    """
    Continuously bridges blocking Pulsar message ingestion to the async queue using thread-safe event loop scheduling.
    """
    while not stop_listener.is_set():
        try:
            msg = consumer.receive(timeout_millis=5000)
            if msg:
                fut = asyncio.run_coroutine_threadsafe(queue.put(msg), loop)
                fut.result()
        except Exception:
            continue

# SHUTDOWN -------------------------------------------------------------------------------------------------------------

async def shutdown():
    """Signals stop, waits for the queue to drain, and gracefully cancels all active tasks."""   
    
    logging.info("shutdown started")
    shutdown_event.set()
    stop_listener.set()  

    await queue.join()

    for task in _active_tasks:
        task.cancel()
        
    # The '*' unpacks the list into individual arguments for gather.
    # 'gather' runs the termination of all tasks concurrently.
    await asyncio.gather(*_active_tasks, return_exceptions=True)
    
    await close_s3()
    logging.info("shutdown complete")

# MAIN -----------------------------------------------------------------------------------------------------------------

async def main():
    # 1. Database Initialization: Creates a connection pool for high-performance PostgreSQL access.
    pool = await asyncpg.create_pool(
        dsn=DATABASE_URL,
        min_size=5,
        max_size=20 #CONCURRENCY
    )
    
    # 2. Storage Setup: Initializes the S3 client connection for file storage or retrieval.
    await init_s3() 
    
    # 3. Messaging Configuration: Subscribes to the Pulsar topic using a Shared consumer type for load balancing.
    client = pulsar.Client(PULSAR_URL)
    consumer = client.subscribe(
        TOPIC,
        subscription_name=SUBSCRIPTION_NAME,
        consumer_type=pulsar.ConsumerType.Shared,
        receiver_queue_size=200,
    )

    loop = asyncio.get_running_loop()
    
    # 4. Bridge Thread: Spawns a daemon thread to bridge blocking Pulsar message ingestion with the async event loop.
    threading.Thread(
        target=listener,
        args=(loop, consumer),
        daemon=True
    ).start()
    
    # 5. Worker Spawning: Creates a set of concurrent tasks to process messages using the connection pool.
    workers = [
        asyncio.create_task(worker_loop(consumer, pool))
        for _ in range(2)
    ]
    
    # 6. Signal Handling: Registers shutdown handlers to ensure clean termination on SIGINT or SIGTERM.
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown()))
        
    # 7. Execution: Concurrently runs all worker tasks and keeps the application alive.
    await asyncio.gather(*workers)

if __name__ == "__main__":
    asyncio.run(main())
