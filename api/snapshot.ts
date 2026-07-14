import type { IncomingMessage, ServerResponse } from "node:http";
import { clientIp, methodNotAllowed, sendJson } from "../lib/http";
import { getRedis } from "../lib/redis";
import { SessionManager } from "../lib/session-manager";

export default async function handler(request: IncomingMessage, response: ServerResponse): Promise<void> {
  if (request.method !== "GET") {
    methodNotAllowed(response);
    return;
  }
  try {
    const manager = new SessionManager({ redis: getRedis() });
    await manager.enforceWsUpgradeRate(clientIp(request));
    const url = new URL(request.url ?? "/", `https://${request.headers.host ?? "localhost"}`);
    const sessionId = url.searchParams.get("session_id") ?? "";
    const viewerToken = url.searchParams.get("viewer_token") ?? request.headers.authorization?.replace(/^Bearer\s+/i, "") ?? "";
    if (!(await manager.validateViewer(sessionId, viewerToken))) {
      sendJson(response, 401, { error: "invalid_viewer_token" });
      return;
    }
    sendJson(response, 200, await manager.getSnapshot(sessionId));
  } catch (error) {
    sendJson(response, 400, { error: error instanceof Error ? error.message : "snapshot_failed" });
  }
}
