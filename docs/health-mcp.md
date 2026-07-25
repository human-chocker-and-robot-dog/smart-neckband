# PC Health MCP V0.2

The Windows PC application now owns the P0 Health MCP implementation. It reads
the same committed ECG, IMU, device-status, and analysis evidence used by the
desktop UI, persists contract-versioned state and events in SQLite, exposes four
read-only tools over stdio, and delivers optional health-event wake-up
notifications through a separate signed webhook queue.

This is an engineering-status interface. It is not a medical device, diagnosis,
emergency service, or authorization for robot motion.

## Install

From the repository PowerShell:

```powershell
.\tools\project.ps1 pc-setup
```

The Health extra pins the official stable MCP Python SDK to `mcp==1.28.0`,
which supports MCP protocol revision `2025-11-25`. The implementation does not
use the prerelease v2 SDK.

## Configuration

Set these variables in the environment that starts the PC GUI and the MCP
child:

```text
SMART_COLLAR_WEARER_ID=<stable-non-name-id>
SMART_COLLAR_HEALTH_DB_PATH=C:\path\outside\Git\health_state.db
```

The GUI starts the Health state worker only when `SMART_COLLAR_WEARER_ID` is
present. The default database path is the ignored local path
`data/health/health_state.db`.
The wearer ID must match `[A-Za-z0-9][A-Za-z0-9._-]{0,63}` and should be a
stable pseudonymous identifier, not a real name. Invalid configured IDs fail
before state or MCP service startup.

The optional health-event webhook requires all three variables:

```text
SMART_COLLAR_HEALTH_WEBHOOK_URL=http://127.0.0.1:8766/v1/health-events
SMART_COLLAR_HEALTH_WEBHOOK_KEY_ID=<health-key-id>
SMART_COLLAR_HEALTH_WEBHOOK_SECRET_HEX=<64-lowercase-hex>
```

The secret is exactly 32 random bytes encoded as 64 lowercase hexadecimal
characters. Uppercase, whitespace, `0x`, Base64, passphrases, and partial
configuration fail closed before a delivery worker starts. Plain HTTP is
accepted only for `localhost`, `127.0.0.1`, or `::1`; every other endpoint must
use HTTPS.

Do not reuse the Live Web token, ordinary Agent webhook token, or an MCP
credential as the health webhook secret.

## Acquisition owner

Start the ordinary PC GUI after setting the environment:

```powershell
.\tools\project.ps1 pc-gui
```

The Health runtime evaluates every 500 ms on a dedicated worker. It writes:

- the latest device snapshot, even before the first ECG packet;
- a wearer-state revision only after a valid ECG packet exists;
- the four P0 event types: `lead_off`, `adc_clipping`, `input_stale`, and
  `input_offline`;
- an independent health webhook outbox in the same transaction as an event
  transition.

The raw recorder still writes each received transport chunk before parsing or
reset decisions. Health state never replaces or filters raw ECG evidence.

## MCP client command

Configure an MCP host to launch the virtual-environment Python directly:

```text
command = C:\path\to\smart-neckband\pc_app\.venv\Scripts\python.exe
args    = -m smart_neckband.health_mcp --transport stdio
env     = SMART_COLLAR_WEARER_ID, SMART_COLLAR_HEALTH_DB_PATH
```

For an interactive shell test, the repository wrapper is:

```powershell
.\tools\project.ps1 pc-health-mcp
```

The server writes only MCP JSON-RPC frames to stdout; operational logs go to
stderr. P0 does not open an MCP network port and does not declare resources,
prompts, or sampling.

The fixed tools are:

- `health.get_current_state`
- `health.get_event_details`
- `health.get_recent_events`
- `health.get_device_status`

All are read-only, idempotent, non-destructive, and closed-world. They do not
return raw or cleaned ECG arrays.

## Local observability

The MCP surface remains exactly four tools. Operational status is available
only through a local, read-only administrator command:

```powershell
.\tools\project.ps1 pc-health-status
```

Set `SMART_COLLAR_WEARER_ID` first. Set `SMART_COLLAR_HEALTH_DB_PATH` when the
runtime uses a non-default database. The command prints JSON containing:

- state revision, source instance, data source, freshness, and recomputed age;
- opened, active, and resolved event counts;
- pending outbox count and oldest pending age;
- webhook attempts, successful deliveries, retries, and dead letters;
- MCP call count, latency, error count, and latest error code;
- parser accepted/lost/CRC counters;
- analysis source, sample index, message, evidence age, and last-success age;
- SQLite migration version.

It deliberately excludes raw webhook bodies, secrets, signatures, waveform
samples, HR, RR, SQI, and other physiological values. A monotonic-clock rollback
is reported conservatively as stale/offline-age evidence rather than as fresh.

## Webhook behavior

Health notifications use only:

```text
POST /v1/health-events
```

They never reuse `/v1/instructions` or `/agent-replies`. Each notification
contains identifiers and transition metadata, not HR, RR, SQI, waveform,
R-peaks, or diagnostic text.

The dispatcher:

- persists canonical UTF-8 body bytes before the first attempt;
- signs `timestamp + "." + raw_body` with HMAC-SHA256;
- disables HTTP redirects;
- retries retryable transport/HTTP failures with jittered backoff;
- preserves per-wearer `notification_sequence` order;
- moves terminal or 24-hour failures to a separate dead-letter table;
- pauses automatic delivery after HTTP 401 or 403.

The receiver must persist and deduplicate before returning HTTP 202, then query
the event and current state through MCP. A notification alone must never drive
physical movement.

## Key rotation

Rotate the webhook key without reusing another application credential:

1. Add the new key ID and 32-byte secret to the receiver while keeping the old
   key temporarily valid.
2. Stop the PC GUI so no Health delivery is in flight.
3. Replace `SMART_COLLAR_HEALTH_WEBHOOK_KEY_ID` and
   `SMART_COLLAR_HEALTH_WEBHOOK_SECRET_HEX` in the process environment.
4. Restart the GUI and confirm a synthetic notification is accepted with the
   new key ID.
5. Remove the old key from the receiver.

Never print either secret, an HMAC signature, or a complete webhook body while
checking rotation.

## Stability soak

The repository includes a synthetic, no-hardware stability runner. It exercises
the state builder at 500 Hz-equivalent input, all four event families, SQLite
writer/reader concurrency, MCP queries, and store recovery:

```powershell
.\tools\project.ps1 pc-health-soak -HealthSoakMinutes 30
```

The command creates an ignored SQLite database and a JSON result under
`data/health/`. A passing result has `"status":"passed"`,
`"mcp_exceptions":0`, a positive state revision, zero production outbox rows,
and a successful reopen of the same database. Synthetic evidence is always
stored as `data_source=synthetic`, `test_mode=true`, and must never enqueue a
production webhook. Outbox ordering, leasing, retry, restart, and hash
invariants are covered by the dedicated automated tests. The soak does not
start serial/BLE hardware, flash a device, or use electrodes.

## Troubleshooting

| Symptom | Meaning and action |
|---|---|
| `WEARER_NOT_FOUND` | The MCP child was started with a different `SMART_COLLAR_WEARER_ID`; correct the environment and restart it. |
| `STATE_UNAVAILABLE` | The GUI has not committed a valid ECG packet yet; inspect device status and acquisition without substituting old physiology. |
| `STATE_STALE` / `STATE_OFFLINE` | ECG evidence exceeded the caller or frozen freshness limit; restore ECG input and wait for a new revision. |
| `DEVICE_OFFLINE` | The reader is closed/error or inbound transport is older than 10 seconds. |
| `RATE_LIMITED` | Respect `retry_after_ms`; the enforced quota is per MCP process and tool. |
| Webhook 401/403 | Automatic health delivery pauses and the item becomes dead letter; fix key ID/secret before restarting the dispatcher. |
| Growing pending outbox | Check receiver availability and TLS/URL policy; do not move health records into the ordinary Agent queue. |
| Old state after Windows restart | It must remain offline until a new packet arrives; a fresh result before new input is a defect. |

For local inspection, keep the DB access limited to the same OS account that
runs the GUI/MCP process. Do not copy the database, raw sessions, logs, or
result files into Git.

## Local deletion

Health MCP exposes no deletion tool. Stop the PC acquisition process, then
generate an exact local deletion plan:

```powershell
pc_app\.venv\Scripts\python.exe -m smart_neckband.health_admin `
  --db C:\path\health_state.db `
  plan-delete `
  --wearer-id <id> `
  --before-utc 2026-07-24T00:00:00.000Z
```

Review the per-table counts. Execute only with the returned confirmation token:

```powershell
pc_app\.venv\Scripts\python.exe -m smart_neckband.health_admin `
  --db C:\path\health_state.db `
  delete `
  --wearer-id <id> `
  --before-utc 2026-07-24T00:00:00.000Z `
  --confirmation-token <token>
```

The delete step obtains an exclusive SQLite transaction, rejects changed
counts, and writes a deletion audit without physiological content. It does not
delete `data/sessions/**/raw.bin`; raw-session deletion is a separate operator
decision.

## Validation and safety

Run:

```powershell
.\tools\project.ps1 pc-test
```

Automated tests use synthetic packet/state fixtures and local temporary
databases. They do not flash firmware, open a serial monitor, or connect body
electrodes.

USB debugging remains electronics-only with no body electrodes. Human ECG
acquisition requires independent battery power and wireless transport; never
attach desktop USB, wall power, a charging power bank, or grounded bench
instruments while electrodes are on a person.
