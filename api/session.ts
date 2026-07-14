import type { IncomingMessage, ServerResponse } from "node:http";
import { requireAdminBearer } from "../lib/auth";
import { clientIp, methodNotAllowed, readJsonBody, sendJson } from "../lib/http";
import { getRedis } from "../lib/redis";
import { SessionManager } from "../lib/session-manager";
import { SessionIdSchema } from "../lib/protocol";

type StopBody = {
  session_id?: string;
};

export default async function handler(request: IncomingMessage, response: ServerResponse): Promise<void> {
  const manager = new SessionManager({ redis: getRedis() });
  try {
    if (request.method === "POST") {
      requireAdminBearer(request.headers.authorization);
      const created = await manager.createSession(clientIp(request));
      sendJson(response, 201, created);
      return;
    }
    if (request.method === "DELETE") {
      requireAdminBearer(request.headers.authorization);
      const body = await readJsonBody<StopBody>(request);
      const parsed = SessionIdSchema.safeParse(body.session_id);
      if (!parsed.success) {
        sendJson(response, 400, { error: "invalid_session_id" });
        return;
      }
      const stopped = await manager.stopSession(parsed.data);
      sendJson(response, stopped ? 200 : 404, { stopped });
      return;
    }
    methodNotAllowed(response);
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown error";
    const status = message.includes("rate limit") ? 429 : message.includes("admin") ? 401 : 400;
    sendJson(response, status, { error: message });
  }
}
