import type { IncomingMessage, ServerResponse } from "node:http";
import { methodNotAllowed, publicBaseUrl, sendJson } from "../lib/http.js";
import { getLiveSessionConfig } from "../lib/live-session.js";

export default function handler(request: IncomingMessage, response: ServerResponse): void {
  if (request.method !== "GET") {
    methodNotAllowed(response);
    return;
  }
  const live = getLiveSessionConfig();
  if (!live) {
    sendJson(response, 503, { error: "live_session_not_configured" });
    return;
  }
  const baseUrl = publicBaseUrl(request) ?? process.env.PUBLIC_BASE_URL ?? "http://localhost:5173";
  const viewerUrl = new URL("/live", baseUrl);
  sendJson(response, 200, {
    type: "live_session",
    session_id: live.sessionId,
    viewer_token: live.viewerToken,
    viewer_url: viewerUrl.toString()
  });
}
