# PC Health MCP V0.3

The single Windows main GUI owns sensor acquisition, NeuroKit2 analysis,
derived metric history, health-event rules, SQLite, and lifecycle control for
the Health MCP server. The RDK runs the Agent and acts as the remote MCP client.

The business MCP surface contains exactly three read-only tools:

```text
health.get_heart_rate
health.get_hrv
health.get_imu_state
```

No MCP tool exposes raw ECG, cleaned ECG, R peaks, raw IMU arrays, event
history, deletion, threshold configuration, secrets, Webhook bodies, diagnosis,
or robot-motion authorization.

## Install

```powershell
.\tools\project.ps1 pc-setup
```

The `health` extra pins `mcp==1.28.0`. Streamable HTTP uses the SDK's server
transport together with Starlette/Uvicorn dependencies installed by the SDK.

## Common configuration

```text
SMART_COLLAR_WEARER_ID=xwen
SMART_COLLAR_HEALTH_DB_PATH=C:\path\outside\Git\health_state.db
SMART_COLLAR_HEALTH_RULES_PATH=C:\path\outside\Git\health_rules.json
```

The wearer ID is a stable pseudonym matching:

```text
[A-Za-z0-9][A-Za-z0-9._-]{0,63}
```

The rules path is optional. Copy `config/health_rules.example.json` to an
ignored local path, set thresholds deliberately, and enable only the required
rules.

## Unified main application

Start the only supported user-facing PC application:

```powershell
.\tools\project.ps1 pc-gui
```

Open the `Health / MCP` tab. It shows the wearer ID, SQLite path, rules file,
HTTP bind address, bearer token, Host allowlist, endpoint, child-process PID,
log path, and the exact three MCP tools. The page can start or stop Health data
production and MCP together. If the environment variables above are complete,
both start automatically with the main GUI.

The `Microphone / Hi ESP`, Webhook, sensor, Health, and MCP functions are tabs
of this same application. The old `pc-mic` command is only a compatibility
alias and now opens `pc-gui`.

## Data ownership

```text
ESP32 serial/BLE packets
  -> Windows GUI/acquisition process
  -> receipt/source-aware buffers
  -> NeuroKit2 HR and RR observations
  -> local IMU motion scoring
  -> SQLite derived history
  -> Health MCP queries and signed Health Webhooks
```

The primary raw ECG recording path is unchanged. Derived MCP history does not
replace raw evidence.

## Tool semantics

Each tool accepts an optional closed input object:

```json
{"window_s": 30}
```

The allowed range is 10 to 300 seconds and the default is 30 seconds.

### `health.get_heart_rate`

Returns latest, mean, minimum, and maximum valid BPM, counts, recent coverage,
mean signal quality, lead-off/clipping observations, and at most one derived
trend point per second.

### `health.get_hrv`

Returns the ultra-short time-domain estimate `rmssd_ms`, `sdnn_ms`,
`pnn50_percent`, `mean_nn_ms`, valid NN count, and quality gates. RMSSD is the
primary value available to local alert rules. Thirty-second HRV is an
engineering estimate and not a medical conclusion.

### `health.get_imu_state`

Returns a 0-100 motion score, still-time percentage, level
(`still/light/moderate/vigorous`), coverage, IMU-online state, method, and a
bounded trend.

The score uses MPU6050 ±2 g and ±250 dps scaling:

```text
acc_activity = abs(norm(acceleration) - 1 g)
gyro_activity = norm(angular_velocity)

score = 100 * (0.6 * clamp(acc_activity / A_REF)
             + 0.4 * clamp(gyro_activity / G_REF))
```

The PC averages per-sample activity into one-second buckets and summarizes the
recent 30-second IMU window. Reference values and rule thresholds require
static, ordinary-motion, and vigorous-motion calibration.

## Local stdio MCP

For development or an Agent host on the same Windows machine:

```powershell
.\tools\project.ps1 pc-health-mcp
```

Equivalent host configuration:

```text
command = C:\path\to\pc_app\.venv\Scripts\python.exe
args = -m smart_neckband.health_mcp --transport stdio
```

## RDK Streamable HTTP MCP

The MCP server remains on Windows because Windows owns SQLite. The RDK connects
to Windows:

```text
RDK Agent -> http://<windows-address>:8765/mcp
Authorization: Bearer <token>
```

Windows configuration:

```text
SMART_COLLAR_HEALTH_MCP_HOST=0.0.0.0
SMART_COLLAR_HEALTH_MCP_PORT=8765
SMART_COLLAR_HEALTH_MCP_PATH=/mcp
SMART_COLLAR_HEALTH_MCP_BEARER_TOKEN=<32-or-more-random-characters>
SMART_COLLAR_HEALTH_MCP_ALLOWED_HOSTS=<windows-ip>:8765,<hostname>:8765
```

The main GUI starts and supervises this server from its `Health / MCP` tab.
For headless automation or diagnostics only, the equivalent command remains:

```powershell
.\tools\project.ps1 pc-health-mcp-http
```

`SMART_COLLAR_HEALTH_MCP_ALLOWED_HOSTS` is checked against the HTTP `Host`
header to reduce DNS-rebinding risk. Configure the Windows firewall manually so
only the RDK address can reach TCP 8765. The project does not modify firewall
rules automatically.

Plain HTTP plus Bearer authentication is acceptable only on an isolated demo
LAN because the token is not encrypted. Production requires HTTPS or an
encrypted private overlay such as Tailscale/WireGuard. Never expose this port
directly to the public Internet.

## Active health Webhook

Health events use a separate channel from ASR text:

```text
ASR/user text: POST /v1/instructions
Health trigger: POST /v1/health-events
```

The Health Webhook is HMAC-SHA256 signed, persisted before delivery, and
retried with stable raw UTF-8 body bytes. It contains the latest trigger
snapshot, not the recent time series. Capability
`health.inspect_recent_metrics` tells the RDK Agent policy to call all three
Health MCP tools with the recommended window.

The receiver maintains two idempotency levels:

- `notification_id + raw_body_sha256` for HTTP retries;
- `event_id + event_revision` for Agent/MCP action opportunities.

The event contract and catalog are:

```text
docs/specs/health-event-bridge-v0.3.contract.json
docs/specs/health-event-catalog.md
```

## Local observability and deletion

Local status remains an administrator command, not an MCP tool:

```powershell
.\tools\project.ps1 pc-health-status
```

Local deletion remains an explicit administrator plan/delete workflow. It is
not exposed to the Agent.

## Validation

```powershell
.\tools\project.ps1 pc-test
.\tools\project.ps1 pc-health-soak -HealthSoakMinutes 5
```

The Health MCP soak is intentionally bounded to five minutes. A canceled run
is reported as user-terminated rather than failed, and its partial SQLite
database is retained for inspection.

No firmware flash, serial monitor, or body-connected acquisition is required
for software validation.
