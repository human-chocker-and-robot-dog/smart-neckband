import { EventEmitter } from "node:events";
import Redis from "ioredis";

export type RedisSetMode = "NX" | "XX";

export interface RedisSubscriber {
  onMessage(handler: (channel: string, message: string) => void): void;
  subscribe(channel: string): Promise<void>;
  unsubscribe(channel: string): Promise<void>;
  quit(): Promise<void>;
}

export interface RedisLike {
  get(key: string): Promise<string | null>;
  set(key: string, value: string, modeOrEx?: "EX" | RedisSetMode, secondsOrMode?: number | RedisSetMode, mode?: RedisSetMode): Promise<"OK" | null>;
  del(...keys: string[]): Promise<number>;
  incr(key: string): Promise<number>;
  expire(key: string, seconds: number): Promise<number>;
  publish(channel: string, message: string): Promise<number>;
  duplicate(): RedisLike & RedisSubscriber;
  ttl?(key: string): Promise<number>;
}

let redisClient: RedisLike | null = null;

export function getRedis(): RedisLike {
  if (redisClient !== null) {
    return redisClient;
  }
  const url = process.env.REDIS_URL ?? process.env.live_heartbeat_REDIS_URL ?? process.env.KV_URL ?? process.env.UPSTASH_REDIS_URL;
  if (!url) {
    throw new Error("REDIS_URL is required for Redis Pub/Sub and session state");
  }
  redisClient = new Redis(url, {
    lazyConnect: true,
    maxRetriesPerRequest: 2,
    enableReadyCheck: true
  }) as unknown as RedisLike;
  return redisClient;
}

export function setRedisForTests(redis: RedisLike | null): void {
  redisClient = redis;
}

type IORedisSubscriber = RedisLike & {
  on(event: "message", handler: (channel: string, message: string) => void): void;
  subscribe(channel: string): Promise<unknown>;
  unsubscribe(channel: string): Promise<unknown>;
  quit(): Promise<unknown>;
};

export function duplicateSubscriber(redis: RedisLike): RedisSubscriber {
  const duplicate = redis.duplicate();
  if ("onMessage" in duplicate && typeof duplicate.onMessage === "function") {
    return duplicate;
  }
  const subscriber = duplicate as unknown as IORedisSubscriber;
  return {
    onMessage(handler) {
      subscriber.on("message", handler);
    },
    async subscribe(channel) {
      await subscriber.subscribe(channel);
    },
    async unsubscribe(channel) {
      await subscriber.unsubscribe(channel);
    },
    async quit() {
      await subscriber.quit();
    }
  };
}

type Entry = {
  value: string;
  expiresAtMs: number | null;
};

type FakeRedisState = {
  store: Map<string, Entry>;
  expireCalls: Map<string, number>;
  bus: EventEmitter;
  nowMs: number;
};

export class FakeRedis implements RedisLike, RedisSubscriber {
  private readonly state: FakeRedisState;
  private readonly listeners = new Map<string, (message: string) => void>();
  private handler: ((channel: string, message: string) => void) | null = null;

  constructor(state?: FakeRedisState) {
    this.state =
      state ??
      {
        store: new Map<string, Entry>(),
        expireCalls: new Map<string, number>(),
        bus: new EventEmitter(),
        nowMs: Date.now()
      };
  }

  get store(): Map<string, Entry> {
    return this.state.store;
  }

  get expireCalls(): Map<string, number> {
    return this.state.expireCalls;
  }

  setNowMs(nowMs: number): void {
    this.state.nowMs = nowMs;
  }

  advanceMs(ms: number): void {
    this.state.nowMs += ms;
  }

  async get(key: string): Promise<string | null> {
    const entry = this.store.get(key);
    if (!entry) {
      return null;
    }
    if (entry.expiresAtMs !== null && entry.expiresAtMs <= this.state.nowMs) {
      this.store.delete(key);
      return null;
    }
    return entry.value;
  }

  async set(key: string, value: string, modeOrEx?: "EX" | RedisSetMode, secondsOrMode?: number | RedisSetMode, mode?: RedisSetMode): Promise<"OK" | null> {
    let expiresAtMs: number | null = null;
    let setMode: RedisSetMode | undefined;
    if (modeOrEx === "EX") {
      expiresAtMs = this.state.nowMs + Number(secondsOrMode) * 1000;
      setMode = mode;
    } else {
      setMode = modeOrEx;
    }
    const exists = (await this.get(key)) !== null;
    if (setMode === "NX" && exists) {
      return null;
    }
    if (setMode === "XX" && !exists) {
      return null;
    }
    this.store.set(key, { value, expiresAtMs });
    if (expiresAtMs !== null) {
      this.expireCalls.set(key, Math.round((expiresAtMs - this.state.nowMs) / 1000));
    }
    return "OK";
  }

  async del(...keys: string[]): Promise<number> {
    let deleted = 0;
    for (const key of keys) {
      if (this.store.delete(key)) {
        deleted += 1;
      }
    }
    return deleted;
  }

  async incr(key: string): Promise<number> {
    const current = Number((await this.get(key)) ?? "0") + 1;
    this.store.set(key, { value: String(current), expiresAtMs: null });
    return current;
  }

  async expire(key: string, seconds: number): Promise<number> {
    const entry = this.store.get(key);
    if (!entry) {
      return 0;
    }
    entry.expiresAtMs = this.state.nowMs + seconds * 1000;
    this.expireCalls.set(key, seconds);
    return 1;
  }

  async publish(channel: string, message: string): Promise<number> {
    this.state.bus.emit(channel, message);
    return this.state.bus.listenerCount(channel);
  }

  duplicate(): RedisLike & RedisSubscriber {
    return new FakeRedis(this.state);
  }

  async ttl(key: string): Promise<number> {
    const entry = this.store.get(key);
    if (!entry) {
      return -2;
    }
    if (entry.expiresAtMs === null) {
      return -1;
    }
    return Math.max(0, Math.ceil((entry.expiresAtMs - this.state.nowMs) / 1000));
  }

  onMessage(handler: (channel: string, message: string) => void): void {
    this.handler = handler;
  }

  async subscribe(channel: string): Promise<void> {
    const listener = (message: string) => {
      this.handler?.(channel, message);
    };
    this.listeners.set(channel, listener);
    this.state.bus.on(channel, listener);
  }

  async unsubscribe(channel: string): Promise<void> {
    const listener = this.listeners.get(channel);
    if (listener) {
      this.state.bus.off(channel, listener);
      this.listeners.delete(channel);
    }
  }

  async quit(): Promise<void> {
    await Promise.all([...this.listeners.keys()].map((channel) => this.unsubscribe(channel)));
  }
}
