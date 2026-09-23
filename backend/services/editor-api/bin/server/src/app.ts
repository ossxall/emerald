import "dotenv/config";
import Fastify from "fastify";
import fastifyWebsocket from "@fastify/websocket";
import Pulsar from "pulsar-client";
import { router } from "./application/router.js";
import { S3Client } from "@aws-sdk/client-s3";
import { Pool } from "pg";
import { Redis } from "ioredis";

export const app = Fastify({ logger: true });
await app.register(fastifyWebsocket);

declare module "fastify" {
  interface FastifyInstance {
    clients: Set<WebSocket>;
    s3: S3Client;
    redis: Redis;
    pg_pool: Pool;
  }
}

const s3Client = new S3Client({
  endpoint: process.env.S3_ENDPOINT as string,
  region: "us-east-1",
  credentials: {
    accessKeyId: process.env.S3_ACCESS_KEY as string,
    secretAccessKey: process.env.S3_SECRET_KEY as string,
  },
  forcePathStyle: true,
});

const redisClient = new Redis(process.env.REDIS_URL!);

const pgPool = new Pool({
  connectionString: process.env.DATABASE_URL,
});

export const pulsarClient = new Pulsar.Client({
  serviceUrl: "pulsar://emerald-broker:6650",
});

export const pulsarProducer = await pulsarClient.createProducer({
  topic: "persistent://public/default/chunk.created",
  batchingEnabled: true,
  batchingType: "KeyBasedBatching", //TODO: implement in base-publisher
});

app.decorate("clients", new Set<WebSocket>());
app.decorate("s3", s3Client);
app.decorate("redis", redisClient);
app.decorate("pg_pool", pgPool);

app.addHook("onClose", async (instance) => {
  await Promise.all([
    instance.s3.destroy(),
    instance.redis.quit(),
    instance.pg_pool.end(),
    pulsarProducer.close(),
  ]);
});

app.register(router, { prefix: "/api/editor" });
