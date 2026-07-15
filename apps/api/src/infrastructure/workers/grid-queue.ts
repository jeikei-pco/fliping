import { Queue, QueueEvents } from "bullmq";
import Redis from "ioredis";

// Reuse the same Redis connection or create a new one
const redisOptions = {
  host: process.env.REDIS_HOST || "127.0.0.1",
  port: parseInt(process.env.REDIS_PORT || "6379", 10),
  maxRetriesPerRequest: null,
};

export const gridRedisConnection = new (Redis as any)(redisOptions);

export const gridQueueName = "GridWorkerQueue";

export const gridQueue = new Queue(gridQueueName, {
  connection: redisOptions,
});

export const gridQueueEvents = new QueueEvents(gridQueueName, {
  connection: redisOptions,
});

export const pingGridWorker = async (): Promise<any> => {
  const job = await gridQueue.add("ping", { timestamp: Date.now() });
  try {
    const result = await job.waitUntilFinished(gridQueueEvents, 5000);
    return result;
  } catch (error: any) {
    throw new Error(`Grid Worker is offline or didn't respond: ${error.message}`);
  }
};

export const dispatchGridEngine = async (action: "start" | "stop" | "auto_start", payload?: any): Promise<any> => {
  const job = await gridQueue.add(action, payload ?? {});
  try {
    // Wait up to 30 seconds for the Python worker to acknowledge start/stop
    const result = await job.waitUntilFinished(gridQueueEvents, 30000);
    return result;
  } catch (error: any) {
    throw new Error(`Failed to ${action} engine: ${error.message}`);
  }
};

export const dispatchGridScan = async (payload: any): Promise<any> => {
  // 🔥 OPTIMIZACIÓN: Retorno inmediato del Job ID
  const job = await gridQueue.add("scan_markets", payload);
  // No esperamos con waitUntilFinished. Devolvemos el ID de la tarea.
  return { jobId: job.id, status: "processing" };
};
