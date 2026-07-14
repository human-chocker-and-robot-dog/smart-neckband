import { createServer, IncomingMessage } from "node:http";
import { randomUUID } from "node:crypto";
import { WebSocket, WebSocketServer } from "ws";
import {
  AuthMessageSchema,
  IngestDataMessageSchema,
  MAX_MESSAGE_BYTES,
  parseJsonMessage,
  ServerEvent,
  serializedSizeBytes
} from "./protocol";
import { clientIp } from "./http";
import { duplicateSubscriber, RedisLike } from "./redis";
import { SessionManager } from "./session-manager";

const AUTH_TIMEOUT_MS = 5_000;
const VIEWER_QUEUE_LIMIT = 32;
const VIEWER_BUFFER_LIMIT_BYTES = 512 * 1024;

export type HeartbeatServerOptions = {
  redis: RedisLike;
  now?: () => number;
  baseUrl?: string;
  wsMaxDurationSeconds?: number;
};

class SocketSendQueue {
  private readonly socket: WebSocket;
  private readonly queue: string[] = [];
  private flushing = false;

  constructor(socket: WebSocket) {
    this.socket = socket;
  }

  enqueue(event: ServerEvent): void {
    const text = JSON.stringify(event);
    if (this.queue.length >= VIEWER_QUEUE_LIMIT) {
      this.queue.shift();
    }
    this.queue.push(text);
    this.flush();
  }

  private flush(): void {
    if (this.flushing || this.socket.readyState !== WebSocket.OPEN) {
      return;
    }
    this.flushing = true;
    const sendNext = () => {
      if (this.socket.readyState !== WebSocket.OPEN) {
        this.queue.length = 0;
        this.flushing = false;
        return;
      }
      if (this.socket.bufferedAmount > VIEWER_BUFFER_LIMIT_BYTES) {
        setTimeout(sendNext, 100);
        return;
      }
      const next = this.queue.shift();
      if (!next) {
        this.flushing = false;
        return;
      }
      this.socket.send(next, () => {
        sendNext();
      });
    };
    sendNext();
  }
}

function sendError(socket: WebSocket, code: string, message: string): void {
  if (socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: "error", code, message }));
  }
}

function payloadLength(data: WebSocket.RawData): number {
  if (typeof data === "string") {
    return Buffer.byteLength(data);
  }
  if (Buffer.isBuffer(data)) {
    return data.length;
  }
  if (data instanceof ArrayBuffer) {
    return data.byteLength;
  }
  return data.reduce((total, item) => total + item.length, 0);
}

export function createHeartbeatServer(options: HeartbeatServerOptions) {
  const manager = new SessionManager({
    redis: options.redis,
    now: options.now,
    baseUrl: options.baseUrl
  });
  const server = createServer((_request, response) => {
    response.writeHead(426, { "content-type": "application/json" });
    response.end(JSON.stringify({ error: "websocket_upgrade_required" }));
  });
  const wss = new WebSocketServer({ server, maxPayload: MAX_MESSAGE_BYTES });

  wss.on("connection", (socket, request) => {
    void handleConnection(socket, request, manager, options.wsMaxDurationSeconds);
  });

  return server;
}

async function handleConnection(
  socket: WebSocket,
  request: IncomingMessage,
  manager: SessionManager,
  wsMaxDurationSeconds = Number(process.env.WS_MAX_DURATION_SECONDS ?? 780)
): Promise<void> {
  try {
    await manager.enforceWsUpgradeRate(clientIp(request));
  } catch {
    sendError(socket, "rate_limited", "too many websocket upgrades");
    socket.close(1008, "rate limited");
    return;
  }

  const url = new URL(request.url ?? "/api/ws", `https://${request.headers.host ?? "localhost"}`);
  const querySessionId = url.searchParams.get("session_id") ?? undefined;
  const connectionOwner = randomUUID();
  let authenticated = false;
  let role: "ingest" | "viewer" | null = null;
  let sessionId: string | null = null;
  let releaseLock: (() => Promise<void>) | null = null;
  let cleanupSubscriber: (() => Promise<void>) | null = null;
  let statusInterval: NodeJS.Timeout | null = null;

  const authTimer = setTimeout(() => {
    if (!authenticated) {
      sendError(socket, "auth_timeout", "auth message required within 5 seconds");
      socket.close(1008, "auth timeout");
    }
  }, AUTH_TIMEOUT_MS);

  const maxDurationTimer = setTimeout(() => {
    sendError(socket, "function_recycle", "websocket function duration reached; reconnect");
    socket.close(1012, "function recycle");
  }, Math.max(1, wsMaxDurationSeconds) * 1000);

  socket.on("message", (data) => {
    void (async () => {
      if (payloadLength(data) > MAX_MESSAGE_BYTES) {
        sendError(socket, "message_too_large", "message exceeds 128 KB");
        socket.close(1009, "message too large");
        return;
      }

      let parsed: unknown;
      try {
        parsed = parseJsonMessage(data);
      } catch {
        sendError(socket, "invalid_json", "message must be JSON");
        socket.close(1003, "invalid json");
        return;
      }

      if (!authenticated) {
        const auth = AuthMessageSchema.safeParse(parsed);
        if (!auth.success) {
          sendError(socket, "invalid_auth", "invalid auth message");
          socket.close(1008, "invalid auth");
          return;
        }
        if (auth.data.role === "ingest") {
          if (!(await manager.validateIngest(auth.data.session_id, auth.data.token))) {
            sendError(socket, "invalid_ingest_token", "invalid ingest token");
            socket.close(1008, "invalid ingest token");
            return;
          }
          if (!(await manager.acquireIngestLock(auth.data.session_id, connectionOwner))) {
            sendError(socket, "producer_exists", "another ingest connection is active for this session");
            socket.close(1008, "producer exists");
            return;
          }
          const ingestSessionId = auth.data.session_id;
          sessionId = ingestSessionId;
          role = "ingest";
          releaseLock = () => manager.releaseIngestLock(ingestSessionId, connectionOwner);
        } else {
          const viewerSessionId = auth.data.session_id ?? querySessionId;
          if (!viewerSessionId || !(await manager.validateViewer(viewerSessionId, auth.data.viewer_token))) {
            sendError(socket, "invalid_viewer_token", "invalid viewer token");
            socket.close(1008, "invalid viewer token");
            return;
          }
          sessionId = viewerSessionId;
          role = "viewer";
          await startViewer(socket, manager, viewerSessionId);
        }
        authenticated = true;
        clearTimeout(authTimer);
        socket.send(JSON.stringify({ type: "auth_ok", role, session_id: sessionId }));
        return;
      }

      if (role !== "ingest" || !sessionId) {
        sendError(socket, "viewer_read_only", "viewer connections cannot upload data");
        socket.close(1008, "viewer read only");
        return;
      }

      const message = IngestDataMessageSchema.safeParse(parsed);
      if (!message.success) {
        sendError(socket, "invalid_message", "invalid ingest message");
        return;
      }
      if (serializedSizeBytes(message.data) > MAX_MESSAGE_BYTES) {
        sendError(socket, "message_too_large", "message exceeds 128 KB");
        socket.close(1009, "message too large");
        return;
      }
      await manager.refreshIngestLock(sessionId, connectionOwner);
      if (message.data.type === "ecg_batch") {
        if (!(await manager.validateSequence(sessionId, message.data.seq))) {
          sendError(socket, "invalid_seq", "sequence must be strictly increasing");
          return;
        }
        await manager.updateFromEcgBatch(sessionId, message.data);
      } else {
        const status = await manager.updateStatus(sessionId, message.data);
        await manager.redis.publish(manager.channel(sessionId), JSON.stringify(status));
      }
    })();
  });

  socket.on("close", () => {
    clearTimeout(authTimer);
    clearTimeout(maxDurationTimer);
    if (statusInterval) {
      clearInterval(statusInterval);
    }
    void cleanupSubscriber?.();
    void releaseLock?.();
  });

  async function startViewer(viewerSocket: WebSocket, sessionManager: SessionManager, viewerSessionId: string): Promise<void> {
    const queue = new SocketSendQueue(viewerSocket);
    queue.enqueue(await sessionManager.getSnapshot(viewerSessionId));
    const subscriber = duplicateSubscriber(sessionManager.redis);
    subscriber.onMessage((_channel, message) => {
      try {
        queue.enqueue(JSON.parse(message) as ServerEvent);
      } catch {
        queue.enqueue({ type: "error", code: "bad_pubsub_message", message: "invalid live message" });
      }
    });
    await subscriber.subscribe(sessionManager.channel(viewerSessionId));
    cleanupSubscriber = async () => {
      await subscriber.unsubscribe(sessionManager.channel(viewerSessionId));
      await subscriber.quit();
    };
    let lastStateJson = "";
    statusInterval = setInterval(() => {
      void (async () => {
        const status = await sessionManager.getPublicStatus(viewerSessionId);
        if (!status) {
          return;
        }
        const text = JSON.stringify(status);
        if (text !== lastStateJson) {
          lastStateJson = text;
          queue.enqueue(status);
        }
      })();
    }, 1_000);
  }
}
