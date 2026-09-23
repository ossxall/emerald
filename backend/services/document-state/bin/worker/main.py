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
SUBSCRIPTION = "snapshot-worker"
POSTGRES_DSN = "postgresql://postgres:password@emerald-postgres:5432/document_state"
REDIS_DOC_TTL = 60 * 60 * 2 

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
        log.info("Connecting to Postgres...")
        self.pg_pool = await asyncpg.create_pool(dsn=POSTGRES_DSN, min_size=5, max_size=20)

        log.info("Connecting to S3...")
        self._s3_cm = aioboto3.Session().client(
            "s3",
            endpoint_url=os.environ.get("S3_ENDPOINT"),
            aws_access_key_id=os.environ.get("S3_ACCESS_KEY"),
            aws_secret_access_key=os.environ.get("S3_SECRET_KEY"),
            region_name=os.environ.get("S3_REGION"),
            config=Config(
                retries={"max_attempts": 3, "mode": "standard"},
                max_pool_connections=12,
                connect_timeout=5,
                read_timeout=30,
            )
        )
        self._s3 = await self._s3_cm.__aenter__()
        
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
        
#------------------------------------------------------------------------------
# S3 SNAPSHOT
#------------------------------------------------------------------------------

    async def _upload_document_snapshot(self, doc_id, new_chunks):
        # 1. Descargar estado actual
        
        doc = await self._get_doc(doc_id)

        # 3. Aplicar los nuevos chunks en orden
        for chunk in new_chunks:
            doc.apply_update(base64.b64decode(chunk["data"]))

        # 4. Serializar el estado final
        snapshot = doc.get_update()
        
        # 5. Subir snapshot a S3
        snapshot_id = uuid7()  
        
        await asyncio.gather(
            self._s3.put_object(
                Bucket="documents",
                Key=f"{doc_id}/snapshots/{snapshot_id}.yjs",
                Body=snapshot,
                ContentType="application/octet-stream"
            ),
            self._s3.put_object(
                Bucket="documents",
                Key=f"{doc_id}/{doc_id}.yjs",  
                Body=snapshot,
                ContentType="application/octet-stream"
            ),
            self._set_doc(doc_id, snapshot)
        )
        
        #TODO:
        #WRITE: document_snapshots.sql with retry
        #WRITE: snapshot.created outbox event 
        #WRITE: redis binary cache.
        
        return snapshot_id

# DATABASE METHODS -----------------------------------------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=2, max=10),  
        retry=retry_if_exception_type(Exception), 
        reraise=True 
    )
    async def insert_outbox(self, conn, document, ts, checksum, metadata):
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
                "document-processor-worker",
                uuid7(),
                ts,
                "document",
                document.get("id"),
                json.dumps(payload),
                json.dumps(metadata),
            )

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
        """Parse, validate, batch insert, acknowledge, and track chunk events using optimized Postgres UNNEST persistence."""

        # 1. Column-oriented buffers required for asyncpg UNNEST batch insertion
        ids, doc_ids, statuses, datas, sources, created_ats = [], [], [], [], [], []
        
        # 2. Map event IDs to original messages for ACK/NACK handling
        msg_map = {}

        for msg in messages:
            try:
                # 3. Deserialize incoming event payload
                event = json.loads(msg.data())
                event_id = uuid.UUID(event.get("event_id"))
                event_data = event.get("data")
                
                 #TODO: ADD TYPE VALIATION OF DATA AND EVENT - TO DLQ
                 
                if not event_id or not event_data:
                    log.warning(f"Event malformed: {event_id}")
                    self.consumer.negative_acknowledge(msg)
                    continue
                
                log.info(event_id)
                
                ids.append(event_id) #event_id == chunk_id
                doc_ids.append(uuid.UUID(event_data.get("document_id")))
                statuses.append(event_data.get("status"))
                datas.append(event_data.get("data"))
                sources.append(event_data.get("source"))
                created_ats.append(event_data.get("created_at"))
                
                # El mapa usa el objeto UUID para comparar con el RETURNING de la DB
                msg_map[event_id] = msg
                
            except Exception as e:
                log.error(f"Error parseando mensaje: {e}")
                self.consumer.negative_acknowledge(msg)
                continue

        if not ids:
            return

        SQL_INSERT_QUERY = """
            INSERT INTO document_chunks (id, document_id, status, data, source, created_at)
            SELECT * FROM unnest(
                $1::uuid[], $2::uuid[], $3::varchar[], $4::text[], $5::varchar[], $6::bigint[]
            )
            ON CONFLICT (id) DO NOTHING
            RETURNING id;
        """

        async with self.pg_pool.acquire() as conn:
            async with conn.transaction():
                try:
                    # Pasamos las listas directamente como argumentos
                    rows = await conn.fetch(
                        SQL_INSERT_QUERY, 
                        ids, doc_ids, statuses, datas, sources, created_ats
                    )
                    
                    inserted_ids = {row['id'] for row in rows}

                    for event_id, msg in msg_map.items():
                        if event_id not in inserted_ids:
                            log.warning(f"Evento duplicado ignorado: {event_id}")
                              
                #TX COMMIT IMPLICIT!                      
                except Exception as e:
                    log.error(f"Error en persistencia batch: {e}")
                    # Importante: No hagas ACK aquí para que el sistema de mensajería reintente el lote
                    raise 
                
        # Outside of the implicit commit        
        for msg in msg_map.values():
            self.consumer.acknowledge(msg)
            
        # IF TX COMMIT SUCCESSFUL
        all_chunk_ids = set(msg_map.keys())    
        async with self._pending_chunk_ids_lock:
            self._pending_chunk_ids.update(all_chunk_ids)          
         
#-----------------------------------------------------------------------------------------------------------------------
# TASK #2 PROCESS CHUNKS
#-----------------------------------------------------------------------------------------------------------------------

    async def _processor_task(self):
        """Tarea que procesa chunks persistidos (ej. subir a S3)"""
        log.info("Procesador de snapshots iniciado.")
        while not self.stop_event.is_set():
            try:
                # Espera 30s o hasta que se pida cerrar
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=30)
                    break # Si el evento se activa, salimos
                except asyncio.TimeoutError:
                    pass

                await self._process_chunks()
            
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Error en procesador: {e}")
                await asyncio.sleep(5)

    async def _process_chunks(self):
            """Lógica de negocio: PENDING -> PROCESSING -> COMPLETED con Transacción"""
            
            async with self._pending_chunk_ids_lock:
                chunk_ids = list(self._pending_chunk_ids)
                
            if not chunk_ids:
                return

            async with self.pg_pool.acquire() as conn:
                # Iniciamos la transacción de DB
                async with conn.transaction():
                    try:
                        # 1. Bloqueamos los chunks (Si falla la función, el rollback los vuelve a PENDING)
                        rows = await conn.fetch("""
                            UPDATE document_chunks
                            SET status = 'PROCESSING'
                            WHERE id = ANY($1::uuid[])
                            AND status = 'PENDING'
                            RETURNING id, document_id, data, created_at
                        """, chunk_ids)

                        if not rows:
                            async with self._pending_chunk_ids_lock:
                                self._pending_chunk_ids.difference_update(chunk_ids)
                                return

                        # 2. Agrupar y procesar en memoria
                        chunks_by_doc = defaultdict(list)
                        for row in rows:
                            chunks_by_doc[row['document_id']].append(row)
                        
                        processed_ids = []

                        for doc_id, doc_chunks in chunks_by_doc.items():
                            # Ordenar por fecha para asegurar consistencia CRDT
                            doc_chunks.sort(key=lambda x: x['created_at'])
                            
                            await self._upload_document_snapshot(doc_id, doc_chunks)
                            
                            for chunk in doc_chunks:
                                processed_ids.append(chunk['id'])
                            
                            
                        # 3. Marcar como COMPLETED dentro de la misma transacción
                        if processed_ids:
                            await conn.execute("""
                                UPDATE document_chunks
                                SET status = 'COMPLETED'
                                WHERE id = ANY($1::uuid[])
                            """, processed_ids)
                            
                        # IMPLICIT TX COMMIT !
                        
                        log.info(f"Transacción exitosa: {len(processed_ids)} chunks procesados.")

                    except Exception as e:
                        # Si algo falla aquí, la transacción hace ROLLBACK automáticamente
                        # Los chunks regresan a 'PENDING' en la DB
                        log.error(f"Error procesando lote. Rollback ejecutado: {e}")
                        raise # Re-lanzamos para evitar limpiar los IDs de la memoria

            # Solo si la transacción fue exitosa, limpiamos los IDs del set de memoria
            async with self._pending_chunk_ids_lock:
                self._pending_chunk_ids.difference_update(chunk_ids)
                
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
                t2 = tg.create_task(self._processor_task())
                
                await self.stop_event.wait()
                log.info("Closing concurrent tasks...")
                t1.cancel()
                t2.cancel()
        except Exception as e:
            log.error(f"TaskGroup Error: {e}")
            
            
    async def close(self):
        log.info("Initiating the closing process...")
        
        if self.consumer:
            self.consumer.close()
            
        if self.client:
            self.client.close()
            
        if self.pg_pool:
            await self.pg_pool.close()
            
        if self._s3_cm:                         
            await self._s3_cm.__aexit__(None, None, None)

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