import { SessionIdSchema, TokenSchema } from "./protocol.js";

export type LiveSessionConfig = {
  sessionId: string;
  ingestToken: string;
  viewerToken: string;
};

export function getLiveSessionConfig(env: NodeJS.ProcessEnv = process.env): LiveSessionConfig | null {
  const sessionId = env.LIVE_SESSION_ID;
  const ingestToken = env.LIVE_INGEST_TOKEN;
  const viewerToken = env.LIVE_VIEWER_TOKEN;
  if (!sessionId || !ingestToken || !viewerToken) {
    return null;
  }
  const parsedSession = SessionIdSchema.safeParse(sessionId);
  const parsedIngest = TokenSchema.safeParse(ingestToken);
  const parsedViewer = TokenSchema.safeParse(viewerToken);
  if (!parsedSession.success || !parsedIngest.success || !parsedViewer.success) {
    return null;
  }
  return { sessionId, ingestToken, viewerToken };
}
