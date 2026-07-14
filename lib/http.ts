import type { IncomingMessage, ServerResponse } from "node:http";

export async function readJsonBody<T>(request: IncomingMessage, maxBytes = 64 * 1024): Promise<T> {
  const chunks: Buffer[] = [];
  let total = 0;
  for await (const chunk of request) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    total += buffer.length;
    if (total > maxBytes) {
      throw new Error("request body too large");
    }
    chunks.push(buffer);
  }
  if (chunks.length === 0) {
    return {} as T;
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8")) as T;
}

export function sendJson(response: ServerResponse, statusCode: number, body: unknown): void {
  response.writeHead(statusCode, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
    "x-robots-tag": "noindex, nofollow"
  });
  response.end(JSON.stringify(body));
}

export function methodNotAllowed(response: ServerResponse): void {
  sendJson(response, 405, { error: "method_not_allowed" });
}

export function clientIp(request: IncomingMessage): string {
  const forwarded = request.headers["x-forwarded-for"];
  const value = Array.isArray(forwarded) ? forwarded[0] : forwarded;
  return value?.split(",")[0]?.trim() || request.socket.remoteAddress || "unknown";
}
