# Vercel Live Heartbeat Backend

## Goal

Implement the AI Smart Collar V0 Live Heartbeat public service on Vercel: a Windows uploader sends NeuroKit2-derived clean ECG, R peaks, HR, SQI, and lead-off status to `/api/ws`; browser viewers connect to the same WebSocket endpoint, receive the latest 10-second snapshot, and watch live updates.

## Current state

- The repository currently contains ESP32 firmware and the Windows Python PC app, but no Node/Vite/Vercel project.
- The PC app already computes clean ECG, R peaks, HR, RR, SQI, lead-off, and packet counters locally.
- Vercel WebSockets are currently Public Beta and run on Vercel Functions with Fluid Compute.
- Redis must be an external Marketplace integration; process-local state is not durable across Vercel Function instances or deployments.
- Raw primary ECG remains owned by the PC app; this service must not implement firmware ECG filtering, NeuroKit2, diagnosis, or long-term raw ECG storage.

## Scope

Included:

- Root TypeScript project with Vite + React frontend and Vercel `api/` functions.
- `/api/session`, `/api/snapshot`, `/api/status`, and `/api/ws`.
- Redis-backed session metadata, token hashes, current status, recent 10-second snapshot, ingest lock, rate limits, and Pub/Sub live channel.
- Zod schemas for auth, ECG batches, status, snapshot, session, and wire messages.
- Token generation with at least 32 random bytes and SHA-256 token hashes in Redis.
- Viewer reconnect, snapshot restore, sequence/timestamp de-duplication, and no high-speed replay of old R-peak animation.
- Tests for sessions, auth, ingest/viewer broadcast, snapshot, reconnection state, stale/offline/lead-off status, TTL, stop session, and no dependency on in-process global state.
- Vercel configuration and environment template without real secrets.

Excluded:

- Independent VPS, Nginx, Docker Compose, FastAPI, ECG filtering, NeuroKit2, medical diagnosis, and long-term raw ECG storage.
- Real Vercel deployment and real Redis provisioning from this local environment.

## Design decisions

- Keep API files exactly under `api/` and shared server/client protocol modules under `lib/`.
- Use Vercel Node.js Functions and the `ws` package; `/api/ws.ts` exports a Node `http.Server` as shown in current Vercel docs.
- Enable Fluid Compute in `vercel.json` and set a conservative WebSocket function `maxDuration`.
- Use Redis keys `session:{id}:meta`, `session:{id}:status`, `session:{id}:snapshot`, `session:{id}:ingest_lock`, and channel `session:{id}:live`.
- Store only token hashes and session metadata in Redis; never log token values or full ECG samples.
- Store only the latest 10 seconds of clean ECG/r-peak data in the snapshot key, with TTL matching the session.
- Use a Redis-backed producer lock so only one ingest connection can be active per session across Function instances.
- Use per-viewer bounded latest-first queues so slow viewer sockets cannot block ingest.
- Treat 3 seconds without ingest data as `stale`, 10 seconds as `offline`, and `lead_off=true` as immediate `signal_lost`.

## Work breakdown

1. Create Node/Vite/TypeScript project files and Vercel configuration.
2. Implement Redis adapter and in-memory fake Redis for tests.
3. Implement auth, session management, protocol schemas, snapshot trimming, status derivation, and rate limits.
4. Implement `/api/session`, `/api/snapshot`, `/api/status`, and `/api/ws`.
5. Implement React viewer client with reconnect/backoff/jitter, snapshot restore, de-duplication, status display, and QR flow.
6. Add Vitest unit/integration coverage.
7. Run `npm test`, `npm run lint`, `npm run typecheck`, `npm run build`, and `git diff --check`.
8. Document deployment, Redis Marketplace configuration, environment variables, Windows uploader example, viewer URL/QR flow, and Vercel Beta risks.

## Validation

Exact commands:

```powershell
npm test
npm run lint
npm run typecheck
npm run build
git diff --check
```

## Risks and rollback

- Vercel WebSockets are Public Beta, and connection lifetime is bounded by Function maxDuration; clients must reconnect and reload snapshot.
- Vercel local development for captured `http.Server` WebSockets may differ from production; tests should exercise the `ws` server logic directly.
- Redis Marketplace providers may inject different URL variable names; `REDIS_URL` is the primary expected variable, with documented alternatives.
- Pub/Sub ordering across reconnects is best effort; sequence/timestamp de-duplication and snapshot reload mitigate duplicates and missed live frames.
- Rollback is removal of the root Node/Vercel files plus the new plan/docs.

## Progress

- [x] Read AGENTS, PLANS, and current repository state.
- [x] Verify current Vercel WebSocket, Fluid Compute, Redis, and limits documentation.
- [x] Scaffold project and Vercel configuration.
- [x] Implement shared protocol/auth/Redis/session modules.
- [x] Implement Vercel APIs and WebSocket flow.
- [x] Implement React viewer.
- [x] Add tests and docs.
- [x] Run validation and summarize results.
- [x] Adapt the viewer into the Live Beta front end with solid background defaults, Canvas particles, real ECG display, and simplified settings.

## Discoveries

- Vercel's current WebSocket docs state that WebSockets are Public Beta on all plans, a connection is pinned to the accepting Function instance, and Fluid Compute lets one Function instance handle multiple WebSocket connections.
- Vercel recommends external storage such as Redis from the Marketplace for persistent state, presence, counters, rooms, and Pub/Sub coordination because reconnects may land on another Function instance.
- Current Vercel Fluid Compute defaults are 300 seconds on Hobby, 300/800 seconds on Pro/Enterprise, with 1800 seconds as a Beta extended maximum for supported Node.js/Python runtimes.
- Vercel Redis docs say Vercel KV is no longer available for new projects; use a Redis Marketplace integration and let it inject credentials/environment variables.
- The implementation uses a root Vite React app plus Vercel `api/` functions rather than Next.js. `/api/ws.ts` exports a Node `http.Server` with `ws`, matching Vercel's current native WebSocket Function example.
- `npm audit --omit=dev` reports 0 production dependency vulnerabilities after installation.
- The crawled reference site defaults to image backgrounds and mutates `document.body.style.background` directly; the production viewer now keeps background rendering behind React settings and explicitly clears `backgroundImage` in solid mode.
- The local reference crawl lives under ignored `data/`; ESLint now ignores `data/**` so reference artifacts are not treated as production source.

## Result

Implemented the Vercel-native Live Heartbeat service:

- TypeScript + Vite + React frontend in `src/`.
- Vercel Node.js Functions in `api/`.
- Shared Redis/auth/protocol/session/WebSocket logic in `lib/`.
- Redis-backed sessions with hashed tokens, 12-hour default TTL, ingest lock, snapshot/status keys, rate limits, Pub/Sub live channel, and stop-session cleanup.
- WebSocket ingest/viewer auth within 5 seconds, 128 KB message limit, Zod validation, 500 Hz sample-rate enforcement, sequence validation, lead-off signal broadcast, and bounded viewer send queues.
- Viewer reconnect with exponential backoff and jitter, re-auth, snapshot restore, sequence/timestamp de-duplication, no localStorage token persistence, noindex/nofollow metadata, and QR generation from the current viewer URL.
- Deployment and Redis Marketplace documentation in `docs/live-heartbeat-vercel.md`.

Verified:

- `npm test`: 11 Vitest tests passed.
- `npm run lint`: passed.
- `npm run typecheck`: passed.
- `npm run build`: passed.
- `git diff --check`: passed.
- `npm audit --omit=dev`: 0 production vulnerabilities.

Not verified:

- No real Vercel deployment was performed.
- No real Redis Marketplace instance was provisioned from this local environment.
- WebSocket behavior on Vercel remains subject to the current Public Beta platform behavior and account maxDuration limits.

Follow-up Live Beta front-end adaptation:

- Replaced the initial utilitarian viewer with `AI Smart Collar V0 · Live Beta`.
- Kept `/api/ws` viewer protocol unchanged and continued using real `snapshot`, `ecg_batch`, `status`, and `signal_lost` messages.
- Added versioned non-sensitive visual settings in localStorage; viewer tokens still only come from the URL.
- Defaulted to pure solid background, disabled ECG grid by default, and did not package crawled reference background images.
- Added Canvas 2D `stars`, `pulse`, and `wave` effects with HR binding only while status is `live`.
- Removed reference-site simulation, audio, system, adaptation, footer warning, ICP, and GitHub concepts from the production viewer.
- Follow-up validation passed: `npm test`, `npm run lint`, `npm run typecheck`, `npm run build`, and `git diff --check`.
