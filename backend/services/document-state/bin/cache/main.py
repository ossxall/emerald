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

from dotenv import load_dotenv
load_dotenv()
from pycrdt import Doc
from uuid6 import uuid7
from collections import defaultdict
from botocore.config import Config
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

import redis.asyncio as aioredis
import os
import asyncio
import json
import logging
import signal
import uuid
import pulsar
import asyncpg
import aioboto3
import base64

# ================= CONFIG =================
PULSAR_URL = "pulsar://emerald-broker:6650"
TOPIC = "persistent://public/default/chunk.created"
SUBSCRIPTION = "snapshot-worker-cache"
POSTGRES_DSN = "postgresql://postgres:password@emerald-postgres:5432/document_state"
REDIS_DOC_TTL = 60 * 60 * 2 
STREAM_MAX_LENGTH=1000
STREAM_TTL_SECONDS = 120


logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger("SnapshotWorker")

# ================= WORKER =================

class SnapshotWorker:
    def __init__(self):
        self.client = pulsar.Client(PULSAR_URL)
        self.consumer = self.client.subscribe(
            TOPIC,
            subscription_name=SUBSCRIPTION,
            consumer_type=pulsar.ConsumerType.KeyShared,
            receiver_queue_size=500,
            unacked_messages_timeout_ms=30000
            #TODO: ADD DLQ
        )
        self.pg_pool = None
        self._s3 = None                         
        self._s3_cm = None
        self._redis: aioredis.Redis | None = None
        
        self.stop_event = asyncio.Event()
        self._pending_chunk_ids: set[uuid.UUID] = set()
        self._pending_chunk_ids_lock = asyncio.Lock()
    
    async def init(self):
        log.info("Connecting to Redis...")
        self._redis = aioredis.Redis.from_url(
            os.environ.get("REDIS_URL"), 
            decode_responses=False,
            max_connections=10,
            socket_connect_timeout=5,
            socket_timeout=10,
            retry_on_timeout=True,
        )
        await self._redis.ping()
        

#------------------------------------------------------------------------------
# REDIS
#------------------------------------------------------------------------------

    async def _get_doc(self, doc_id: uuid.UUID) -> Doc:
        """Carga el Doc Yjs desde Redis o S3, en ese orden."""
        cache_key = f"doc:{doc_id}:state"
        
        try:
            cached = await self._redis.get(cache_key)
            if cached:
                log.info(f"Cache HIT para doc_id={doc_id}")
                doc = Doc()
                doc.apply_update(cached)
                return doc
        except Exception as e:
            log.info(f"Cache MISS para doc_id={doc_id}, cargando desde S3...")
        
        try:
            response = await self._s3.get_object(
                Bucket="documents",
                Key=f"{doc_id}/{doc_id}.yjs"
            )
            existing = await response['Body'].read()
            doc = Doc()
            doc.apply_update(existing)
            return doc
        except self._s3.exceptions.NoSuchKey:
            raise ValueError(f"Original YJS binary not found for doc_id={doc_id}")
            
    async def _set_doc(self, doc_id: uuid.UUID, snapshot: bytes):
        """Persiste el snapshot en Redis con TTL."""
        cache_key = f"doc:{doc_id}:state"
        await self._redis.set(cache_key, snapshot, ex=REDIS_DOC_TTL)
        
#-----------------------------------------------------------------------------------------------------------------------
# TASK #1 EVENT CONSUMER
#-----------------------------------------------------------------------------------------------------------------------

    async def _consume_task(self):
        """Resilient consumer loop: receives message batches and persists chunks to Postgres."""
        log.info("Consumer loop started.")
        while not self.stop_event.is_set():
            try:
                # 1. batch_receive is blocking, so execute it in a worker thread
                messages = await asyncio.to_thread(self.consumer.batch_receive)
                if not messages:
                    await asyncio.sleep(0.1)
                    continue
                
                 # 2. Persist received chunks into Postgres
                try: 
                    await self._persist_chunks(messages)        
                except Exception as e:
                    log.error(f"Error persisting in batch: {e}")
                    
                    for msg in messages:
                        self.consumer.negative_acknowledge(msg)
                        
                    await asyncio.sleep(1) 
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.exception(f"Fatal consumer error: {e}")
                await asyncio.sleep(2)

    async def _persist_chunks(self, messages):
        """add chunks to redis stream."""
        pipe = self._redis.pipeline(transaction=False)
        
        processed = 0
        
        for msg in messages:
            try:
                event = json.loads(msg.data())
                event_data = event["data"]
                
                document_id = event_data["document_id"]

                chunk_bytes = base64.b64decode(event_data["data"])
    
                stream_key = f"doc:{document_id}:chunks"
                pipe.xadd(
                    stream_key,
                    {
                        b"data": chunk_bytes,
                    },
                    maxlen=STREAM_MAX_LENGTH,
                    approximate=True
                )
                pipe.expire(
                    stream_key,
                    STREAM_TTL_SECONDS,
                )
                
                processed += 1
                
            except Exception:
                logging.exception("invalid message")
        
        await pipe.execute()

        for msg in messages:
            self.consumer.acknowledge(msg)

        logging.info("processed batch=%s", processed)        
        
      
         
#-----------------------------------------------------------------------------------------------------------------------
# RUN
#-----------------------------------------------------------------------------------------------------------------------

    def _shutdown(self):
        log.info("Shutdown signal received...")
        self.stop_event.set()
        
        
    async def run(self):
        # 1. Add signal handlers.
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._shutdown)
            except NotImplementedError: 
                pass
            
        # 2. Create worker tasks. 
        try:
            async with asyncio.TaskGroup() as tg:
                t1 = tg.create_task(self._consume_task())

                await self.stop_event.wait()
                log.info("Closing concurrent tasks...")
                t1.cancel()
        except Exception as e:
            log.error(f"TaskGroup Error: {e}")
            
            
    async def close(self):
        log.info("Initiating the closing process...")
        
        if self.consumer:
            self.consumer.close()
            
        if self.client:
            self.client.close()
            
        if self._redis:
            await self._redis.aclose()
                        
        log.info("Worker shut down properly.")           
            
#-----------------------------------------------------------------------------------------------------------------------
# MAIN 
#-----------------------------------------------------------------------------------------------------------------------

async def main():
    worker = SnapshotWorker()
    await worker.init()
    try:
        await worker.run()
    finally:
        await worker.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass