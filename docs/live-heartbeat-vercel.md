# AI Smart Collar V0 Live Heartbeat on Vercel

This service is a Vercel-native realtime backend and viewer for PC-derived heartbeat and Health Dashboard data.

Flow:

```text
Windows uploader
-> /api/ws Vercel WebSocket Function
-> Redis Pub/Sub
-> /api/ws Vercel WebSocket Function
-> viewer browser
```

The uploader sends NeuroKit2-derived clean ECG, R peaks, HR, SQI, and lead-off state. The service does not filter ECG, run NeuroKit2, diagnose medical conditions, or store long-term raw ECG.

The independent `/dashboard` route additionally receives derived IMU attitude,
the existing 0-100 motion score, bounded device diagnostics, UWB distance state,
and recent engineering health events. It does not expose raw IMU arrays, voice
content, MCP calls, dog-state inference, or medical conclusions.

## Vercel Deployment

1. Create or select a Vercel project for this repository.
2. Confirm Vercel auto-detects the TypeScript files under `api/` as Node.js Functions.
3. Enable Fluid Compute in the Vercel dashboard under Functions settings. `vercel.json` also sets `"fluid": true`.
4. Set environment variables listed below.
5. Deploy with the Vercel Git integration or:

```powershell
npm install
npm test
npm run lint
npm run typecheck
npm run build
vercel deploy --prod
```

`api/ws.ts` exports a Node `http.Server` using `ws`, matching current Vercel WebSocket Function examples. `vercel.json` does not set a custom `runtime` string because TypeScript files under `api/` are auto-detected as Node.js Functions; it only sets `maxDuration` for `api/ws.ts` to 300 seconds because the current account limit rejects higher values. Account limits still apply.

## Redis Marketplace Setup

1. In the Vercel dashboard, open the project.
2. Go to Marketplace and install a Redis provider integration.
3. Provision a Redis database and link it to this project.
4. Confirm Vercel injected a Redis URL environment variable.
5. Prefer a TCP Redis URL compatible with `ioredis` and Redis Pub/Sub, exposed as `REDIS_URL`.
6. Redeploy after environment variables are available.

Required Redis features:

- `GET`, `SET EX`, `SET NX EX`, `DEL`, `INCR`, `EXPIRE`
- Pub/Sub `PUBLISH` / `SUBSCRIBE`

## Environment Variables

| Name | Required | Notes |
| --- | --- | --- |
| `ADMIN_TOKEN` | yes | Long random admin token for session creation and stop. |
| `REDIS_URL` | yes | Redis Marketplace TCP URL for `ioredis` and Pub/Sub. |
| `live_heartbeat_REDIS_URL` | no | Supported custom-prefix alias for `REDIS_URL`. |
| `LIVE_SESSION_ID` | recommended | Fixed public `/live` room id, for example `sess_live_main_...`. |
| `LIVE_INGEST_TOKEN` | recommended | Private token used by the Windows uploader for `/live`. |
| `LIVE_VIEWER_TOKEN` | recommended | Viewer token used by `/api/live-session`; public viewers do not see it in the URL. |
| `LIVE_WS_URL` | Windows PC | Absolute `wss://.../api/ws` URL used by the main-GUI Dashboard relay. |
| `SMART_COLLAR_DASHBOARD_PUBLIC_URL` | no | Public `/dashboard` URL shown in the PC GUI. |
| `PUBLIC_BASE_URL` | no | Optional override for `viewer_url`; when omitted, the API derives the origin from forwarded Vercel request headers. |
| `SESSION_TTL_SECONDS` | no | Defaults to `43200` seconds / 12 hours. |
| `SESSION_CREATE_LIMIT_PER_MINUTE` | no | Defaults to `10` per IP. |
| `WS_UPGRADE_LIMIT_PER_MINUTE` | no | Defaults to `60` per IP. |
| `WS_MAX_DURATION_SECONDS` | no | Defaults to `280`; choose a value below the configured Vercel maxDuration. |

Never commit real tokens, Redis URLs, or production domains.

## Fixed Public Live Page

The permanent public viewer is the configured custom-domain `/live` page:

```text
https://<public-host>/live
```

`/live` does not require query parameters. It calls `GET /api/live-session`, receives the configured `LIVE_SESSION_ID` and `LIVE_VIEWER_TOKEN` in memory, then connects to `/api/ws`. The viewer token is not written to `localStorage` and the address bar stays stable.

The matching uploader connects with:

```text
LIVE_SESSION_ID + LIVE_INGEST_TOKEN -> wss://<public-host>/api/ws
```

The fixed live room does not rely on Redis session metadata expiring. Redis still stores the current snapshot, status, producer lock, sequence, and Pub/Sub live channel. If upload stops, the public page becomes stale/offline instead of replaying old ECG.

## Fixed Public Health Dashboard

The event-demo QR target is:

```text
https://<public-host>/dashboard
```

It uses the same fixed live-session discovery as `/live`; the viewer token is
returned to browser memory by `/api/live-session` and never appears in the URL
or `localStorage`. `/live` and `/viewer` remain unchanged.

For an explicitly marked, deterministic presentation fallback use:

```text
https://<public-host>/dashboard?demo=1
```

Demo mode never activates automatically. The page continuously displays
`演示数据 · 非真实采集`, and its scripted UWB distance is not mixed with live
measurements.

The Windows main GUI starts the Dashboard relay automatically when these three
variables are configured together:

```text
LIVE_WS_URL=wss://<public-host>/api/ws
LIVE_SESSION_ID=sess_live_main_...
LIVE_INGEST_TOKEN=<private-ingest-token>
```

The relay reads the GUI's existing `PcDataStores`, ECG analysis, attitude
worker, and Health event store. It must not be run alongside the standalone
`smart_neckband.live_uploader` against the same session: the server intentionally
allows only one ingest connection, and the standalone command opens its own
serial reader. Relay endpoint, connection state, last successful upload, public
URL, and errors are visible on the PC application's `Health / MCP` tab; tokens
are never rendered or logged.

Real UWB hardware is not connected in this version. The PC publishes
`source=unavailable` with null distance and quality through the stable
`DistanceProvider` interface. A later real adapter can populate that interface
without changing the browser protocol.

## Admin Session API

Create:

```powershell
$body = '{}'
Invoke-RestMethod `
  -Method POST `
  -Uri 'https://your-project.vercel.app/api/session' `
  -Headers @{ Authorization = "Bearer $env:ADMIN_TOKEN" } `
  -ContentType 'application/json' `
  -Body $body
```

The response contains:

```json
{
  "session_id": "sess_...",
  "ingest_token": "...",
  "viewer_token": "...",
  "viewer_url": "https://your-project.vercel.app/viewer?session_id=...&viewer_token=...",
  "expires_at_ms": 178...
}
```

Stop:

```powershell
Invoke-RestMethod `
  -Method DELETE `
  -Uri 'https://your-project.vercel.app/api/session' `
  -Headers @{ Authorization = "Bearer $env:ADMIN_TOKEN" } `
  -ContentType 'application/json' `
  -Body (@{ session_id = 'sess_...' } | ConvertTo-Json)
```

Stopping a session deletes Redis status, ECG snapshot, telemetry, recent-event,
ingest-lock, and last-sequence keys, then publishes `session_stopped`.

## Windows Uploader Example

The uploader connects to `/api/ws`, authenticates within 5 seconds, then sends clean ECG batches and status updates. Do not send raw firmware ECG or full local recordings to this public service.

For the checked-in PC app, install upload extras once:

```powershell
cd C:\Users\XWen1024\Documents\smart-neckband\pc_app
py -3.12 -m pip install -e .[upload]
```

Then start the fixed live uploader:

```powershell
cd C:\Users\XWen1024\Documents\smart-neckband\pc_app
$env:LIVE_SESSION_ID = "sess_live_main_..."
$env:LIVE_INGEST_TOKEN = "..."
py -3.12 -m smart_neckband.live_uploader `
  --port COM19 `
  --ws-url "wss://<public-host>/api/ws" `
  --session-id $env:LIVE_SESSION_ID `
  --ingest-token $env:LIVE_INGEST_TOKEN
```

The uploader prints one health line per second with serial packet count, ECG buffer size, acknowledged upload batches/samples, HR, SQI, lead-off, packet loss, CRC errors, analysis duration, and analysis state. Tokens and ECG sample values are never logged. Use `--duration 20` for a bounded diagnostic run and `--log-level DEBUG` for connection troubleshooting.

The uploader reads the Bluetooth SPP COM port, keeps raw binary logging optional, runs NeuroKit2 on a separate PC worker thread, uploads only incremental clean ECG samples, maps R peaks into each uploaded batch, and sends status with lead-off, packet loss, and CRC counters. Ingest authentication returns Redis's `next_seq`, and every upload must receive an acknowledgement; restarts therefore continue the existing session sequence instead of silently replaying sequence `0`.

The first Python process can spend about seven seconds importing NeuroKit2 and NumPy. Serial reading, status updates, and health logs continue during that warmup; subsequent 10-second ECG analyses take tens of milliseconds on the confirmed Windows machine.

The lower-level protocol example is:

```python
import json
import time
import websocket

session_id = "sess_..."
ingest_token = "..."
ws = websocket.create_connection("wss://your-project.vercel.app/api/ws")
ws.send(json.dumps({
    "type": "auth",
    "role": "ingest",
    "session_id": session_id,
    "token": ingest_token,
}))

seq = 0
while True:
    clean_ecg = [0.0] * 20  # replace with NeuroKit2-cleaned values from the PC app
    r_peaks = []            # indices within the current batch/window representation
    ws.send(json.dumps({
        "type": "ecg_batch",
        "seq": seq,
        "timestamp_ms": int(time.time() * 1000),
        "sample_rate": 500,
        "samples": clean_ecg,
        "r_peaks": r_peaks,
        "hr_bpm": None,
        "sqi": None,
        "lead_off": False,
    }))
    seq += 1
    time.sleep(0.04)
```

Rules enforced by the server:

- JSON message size must be at most 128 KB.
- `sample_rate` must be `500`.
- `seq` must strictly increase per session.
- Samples are not modified, interpolated, filtered, or stored long-term.
- Only one active ingest connection is allowed per session.
- Telemetry contains derived values only and is capped by the 128 KB message limit.
- Recent events are deduplicated by `event_id + event_revision` and capped at 20 per session.

## Viewer URL and QR Flow

1. Create a session with `POST /api/session`.
2. Share the returned `viewer_url`.
3. The React viewer generates a QR code from the exact current URL in memory.
4. The viewer token stays in the URL for the current session and is not written to `localStorage`.
5. The page and Vercel headers are marked `noindex, nofollow`.

On connect or reconnect, the viewer:

- opens `wss://host/api/ws?session_id=...`;
- sends `{ "type": "auth", "role": "viewer", "viewer_token": "..." }`;
- receives a `snapshot` message with `snapshot=true`;
- resumes live `ecg_batch` messages;
- de-duplicates by `seq` and `timestamp_ms`;
- does not high-speed replay old R-peak animation from snapshots.

## Status Behavior

- No ingest data for 3 seconds: `stale`.
- No ingest data for 10 seconds: `offline`.
- `lead_off=true`: `signal_lost` immediately.
- Offline viewers keep the last static snapshot but do not loop old ECG.
- Snapshot data always carries `snapshot=true`.

## Redis Keys

```text
session:{id}:meta
session:{id}:status
session:{id}:snapshot
session:{id}:telemetry
session:{id}:recent_events
session:{id}:ingest_lock
session:{id}:last_seq
session:{id}:live
```

All session keys have TTL. Pub/Sub channels are not durable.

## Vercel Beta Risks

- Vercel WebSockets are Public Beta; behavior and APIs may change.
- WebSocket connections close when the Function reaches max duration. Clients must reconnect and reload snapshot.
- Reconnects may land on a different Function instance or deployment, so Redis must remain the source of truth.
- Long maxDuration values depend on Vercel account plan; extended 1800-second durations are Beta and account/runtime dependent.
- Redis provider latency and Pub/Sub delivery are outside the Function process; live updates are realtime best effort.
