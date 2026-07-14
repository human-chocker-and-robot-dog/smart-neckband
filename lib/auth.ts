import { createHash, randomBytes, timingSafeEqual } from "node:crypto";

export function createToken(): string {
  return randomBytes(32).toString("base64url");
}

export function createSessionId(): string {
  return `sess_${randomBytes(18).toString("base64url")}`;
}

export function hashToken(token: string): string {
  return createHash("sha256").update(token, "utf8").digest("base64url");
}

export function safeEqualHash(token: string, expectedHash: string): boolean {
  const actual = Buffer.from(hashToken(token), "utf8");
  const expected = Buffer.from(expectedHash, "utf8");
  return actual.length === expected.length && timingSafeEqual(actual, expected);
}

export function requireAdminBearer(header: string | string[] | undefined): void {
  const expected = process.env.ADMIN_TOKEN;
  if (!expected) {
    throw new Error("ADMIN_TOKEN is not configured");
  }
  const value = Array.isArray(header) ? header[0] : header;
  const prefix = "Bearer ";
  if (!value?.startsWith(prefix)) {
    throw new Error("missing admin bearer token");
  }
  const token = value.slice(prefix.length);
  const actual = Buffer.from(token, "utf8");
  const wanted = Buffer.from(expected, "utf8");
  if (actual.length !== wanted.length || !timingSafeEqual(actual, wanted)) {
    throw new Error("invalid admin bearer token");
  }
}
