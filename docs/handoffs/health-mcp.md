# Smart Collar Health MCP Handoff

> Integration note (2026-07-25): the active hardware baseline is now ESP32-C3
> only. References to classic ESP32/SPP below describe branch history or frozen
> contract compatibility, not an active firmware target.

Last updated: 2026-07-25.

This handoff is for the upcoming large integration pass. Its purpose is to let
another agent merge the Health MCP work by reusing the existing repository
implementation, not by rewriting the feature from prose.

The reviewed Health MCP implementation baseline captured by this handoff is
`feat/health-mcp-v0` at:

```text
e8373bcc315b0523a0a53e11adde575c0a59b6cf
```

The branch has already been pushed to `origin/feat/health-mcp-v0`. This file may
be followed by a docs-only handoff commit on the same branch.

## Merge Intent

Keep the Health MCP implementation as the source of truth for all PC-side
health-state, event, SQLite, MCP, and signed health-webhook behavior.

Do not reimplement these features during the large merge. Reuse the modules,
tests, contracts, and documented commands listed below. If a target branch has
newer microphone, ASR, BLE, or GUI work, merge those newer user-facing changes
around the Health MCP files instead of replacing the Health MCP design with a
new copy.

The Health MCP server is not a device reader. The GUI/acquisition process owns
serial or BLE input, builds committed Health state, and writes SQLite. The MCP
process is a read-only stdio child that reads that SQLite database.

## Branch And Commit Context

Canonical repository:

```text
https://github.com/human-chocker-and-robot-dog/smart-neckband.git
```

Important commits:

| Purpose | Commit |
|---|---|
| Main baseline before webhook/voice/health | `88b841be6f1dc1fdc7f5071769d7a919effbb290` |
| Ordinary Agent webhook baseline inherited by Health branch | `c08e696` |
| Reliable voice text transport inherited by Health branch | `17fc0df` |
| Offline wake and streaming ASR baseline inherited by Health branch | `abe97defef27bda47b6b1a2163e8908eb1df6d6d` |
| Health contract fixtures | `1425d55` |
| Staged packet handling, reset coordination, receipts, ECG ordinals | `0f96a8b` |
| Health MCP runtime, SQLite store, stdio MCP, health webhook | `340c5ea` |
| Final Health delivery record | `e8373bc` |

Current other large-merge peer observed locally:

```text
test/inmp441-ble-capture
e1814c54ada449321a30b710b914687ef5e1a7f3
```

`feat/health-mcp-v0` and `test/inmp441-ble-capture` both diverge from
`abe97de`. The microphone branch has newer voice, ASR, and INMP441 work after
that point. Do not let the Health branch roll those newer microphone changes
back.

`git merge-tree HEAD test/inmp441-ble-capture` reported one content conflict:

```text
tools/project.ps1
```

Other shared files may auto-merge but still require review, especially
`pc_app/pyproject.toml`, `pc_app/src/smart_neckband/gui.py`,
`pc_app/src/smart_neckband/ble_io.py`, `pc_app/src/smart_neckband/serial_io.py`,
`pc_app/src/smart_neckband/protocol.py`, `pc_app/src/smart_neckband/buffers.py`,
and their tests.

## Contract Files

These files are the contract authority. Reuse them as committed artifacts.

| File | Role | SHA-256 |
|---|---|---|
| `docs/specs/Smart_Collar_Health_MCP_Spec_v0.2.md` | Human-readable v0.2 spec | `E1D035EDE3040689AEB0F887EE79E0B35E76D3D80A062AC74566215AE60D5533` |
| `docs/specs/health-mcp-v0.2.contract.json` | Machine contract, schemas, goldens | `D9EC1619A14A0A7E38F2384C9B7F248B1D9EE3147DB9BD2AFF5C8DC8057F3626` |
| `docs/health-mcp.md` | Operator guide | `5B1762A04004673B2EE75188F9E01F407942FA88C968BBFFC036E081BD850B1D` |

The runtime loads the machine contract through
`pc_app/src/smart_neckband/health_contract.py`. Tool schemas exposed by
`tools/list` come from `x-mcp-tools` in the JSON contract. Do not manually
duplicate those schemas elsewhere.

Fixed contract facts:

| Item | Value |
|---|---|
| Health schema version | `0.2.0` |
| MCP protocol baseline | `2025-11-25` |
| Python MCP SDK pin | `mcp==1.28.0` |
| Transport in this branch | stdio only |
| MCP resources/prompts/sampling | not implemented in P0 |
| Health event webhook path | `/v1/health-events` |
| Health event types | `lead_off`, `adc_clipping`, `input_stale`, `input_offline` |

## Architecture

The intended data flow is:

```text
serial/BLE reader
-> PacketParser staged decode
-> SourceInstanceCoordinator commit gate
-> PC ring buffers and NeuroKit2 analysis provenance
-> HealthRuntimeWorker every 500 ms
-> HealthStore SQLite WAL database
-> read-only Health MCP stdio process
-> optional signed health-event webhook dispatcher
```

The raw transport recorder still records inbound chunks before parse or reset
decisions. Health state must never replace the raw ECG evidence path.

The Health MCP implementation is PC-side only. It does not change firmware,
packet wire format, GPIO, sample rates, flash layout, or hardware validation.

## Source Map

Reuse these files directly.

| Area | Files |
|---|---|
| Contract loader and wearer validation | `pc_app/src/smart_neckband/health_contract.py` |
| MCP stdio server and tool service | `pc_app/src/smart_neckband/health_mcp.py` |
| GUI-side runtime worker | `pc_app/src/smart_neckband/health_runtime.py` |
| State document builder | `pc_app/src/smart_neckband/health_state.py` |
| Signal quality and 10-second windows | `pc_app/src/smart_neckband/health_quality.py` |
| SQLite migrations, state/events/outbox/audit/admin deletion | `pc_app/src/smart_neckband/health_store.py` |
| Signed webhook sender, dispatcher, mock receiver | `pc_app/src/smart_neckband/health_webhook.py` |
| Local status and deletion CLI | `pc_app/src/smart_neckband/health_admin.py` |
| Synthetic no-hardware soak runner | `pc_app/src/smart_neckband/health_soak.py` |
| Source reset and ECG ordinal ownership | `pc_app/src/smart_neckband/source_coordinator.py` |
| Receipt-aware buffers | `pc_app/src/smart_neckband/buffers.py` |
| Staged parser commit accounting | `pc_app/src/smart_neckband/protocol.py` |
| Serial integration | `pc_app/src/smart_neckband/serial_io.py` |
| BLE integration | `pc_app/src/smart_neckband/ble_io.py` |
| Analysis provenance fields | `pc_app/src/smart_neckband/analysis.py` |
| GUI worker startup | `pc_app/src/smart_neckband/gui.py` |
| Project wrapper commands | `tools/project.ps1` |
| Dependency pin | `pc_app/pyproject.toml` |
| Environment examples | `.env.example` |

The `health_*` modules are not enough by themselves. Health correctness also
depends on `source_coordinator.py` plus the staged parser, receipt-aware buffer,
serial/BLE, analysis, and GUI changes.

## Runtime Responsibilities

`HealthRuntimeWorker.from_environment()` is called from
`pc_app/src/smart_neckband/gui.py`. It starts only when
`SMART_COLLAR_WEARER_ID` is set. Invalid wearer IDs fail before the worker or
MCP service starts.

The runtime writes:

| Output | Rule |
|---|---|
| Device snapshot | Can exist before the first ECG packet |
| Wearer state | Created only after a valid ECG packet exists |
| Events | Four fixed P0 event families only |
| Health outbox | Enqueued in the same transaction as event transitions |
| Observability | Parser, analysis, delivery, MCP audit, migration status |

The wearer ID pattern is:

```text
[A-Za-z0-9][A-Za-z0-9._-]{0,63}
```

Use a stable pseudonymous ID, not a real name.

## MCP Surface

The MCP server is launched as a child process. Recommended host configuration:

```text
command = C:\path\to\smart-neckband\pc_app\.venv\Scripts\python.exe
args    = -m smart_neckband.health_mcp --transport stdio --db C:\path\to\health_state.db --wearer-id xwen
```

The wrapper command is:

```powershell
.\tools\project.ps1 pc-health-mcp
```

The four fixed tools are:

| Tool | Purpose |
|---|---|
| `health.get_current_state` | Latest full wearer state |
| `health.get_event_details` | Authoritative event by `event_id` |
| `health.get_recent_events` | Paged event history |
| `health.get_device_status` | Device and transport status without requiring HR/SQI |

All tools are read-only, idempotent, non-destructive, and closed-world. They do
not return raw ECG arrays, cleaned ECG arrays, R-peak arrays, secrets,
signatures, or webhook bodies.

Errors inside valid tool execution are returned in the contract envelope with
`isError=true`. Unknown tools and malformed JSON-RPC requests return JSON-RPC
errors. This distinction is tested and should not be simplified.

Rate limits per MCP process:

| Tool | Calls per minute |
|---|---|
| `health.get_current_state` | 120 |
| `health.get_event_details` | 120 |
| `health.get_recent_events` | 30 |
| `health.get_device_status` | 60 |

## SQLite Store

The Health database is local SQLite in WAL mode. Default ignored path:

```text
data/health/health_state.db
```

Recommended explicit environment:

```text
SMART_COLLAR_WEARER_ID=xwen
SMART_COLLAR_HEALTH_DB_PATH=C:\path\outside\Git\health_state.db
```

Important tables are created by `HealthStore._initialize()`:

| Table | Purpose |
|---|---|
| `health_schema` | Migration version |
| `health_wearer_sequences` | Per-wearer state and notification counters |
| `health_states` | Latest contract-versioned state |
| `health_device_snapshots` | Device status even before ECG |
| `health_events` | Active/resolved events |
| `health_event_gates` | Event hysteresis gates |
| `health_webhook_outbox` | Pending signed notifications |
| `health_webhook_dead_letters` | Terminal delivery failures |
| `health_webhook_deliveries` | Delivery success audit |
| `health_mcp_audit` | MCP calls, latency, outcome |
| `health_runtime_observability` | Parser and analysis status |
| `health_deletion_audit` | Local deletion audit without physiology |

Local deletion is intentionally not exposed through MCP. Use
`pc_app/src/smart_neckband/health_admin.py` through the documented plan/delete
flow in `docs/health-mcp.md`.

## Health Webhook

The health webhook is separate from the ordinary Agent instruction/reply
webhook. Do not reuse:

- ordinary Agent endpoint paths;
- ordinary Agent queues;
- ordinary Agent deduplication keys;
- Live Web upload tokens;
- MCP credentials.

Optional sender environment requires all three variables together:

```text
SMART_COLLAR_HEALTH_WEBHOOK_URL=http://127.0.0.1:8766/v1/health-events
SMART_COLLAR_HEALTH_WEBHOOK_KEY_ID=<health-key-id>
SMART_COLLAR_HEALTH_WEBHOOK_SECRET_HEX=<64-lowercase-hex>
```

The secret must decode to exactly 32 bytes. HTTP is allowed only for loopback;
other endpoints must use HTTPS.

The sender persists canonical UTF-8 body bytes and a SHA-256 before first
attempt, then signs `timestamp + "." + raw_body` with HMAC-SHA256 on each
delivery attempt. Redirects are disabled. Per-wearer notification ordering is
enforced by the store.

Synthetic and replay data must use `data_source=synthetic` or `replay` with
`test_mode=true`; those states must not create production outbox rows.

Receiver-side reference behavior is implemented for tests in
`HealthWebhookReceiver` and `HealthWebhookReceiverStore` inside
`pc_app/src/smart_neckband/health_webhook.py`.

## Packet And Source Boundary Changes

Do not remove the staged packet path during merge.

Key ownership:

| Owner | Responsibility |
|---|---|
| `PacketParser.feed()` | Decode CRC-valid frames without committing sequence/loss accounting |
| `PacketParser.commit_packet()` | Commit parser stats only after source coordinator acceptance |
| `SourceInstanceCoordinator` | Detect and confirm source resets, buffer/replay packets, rotate `source_instance_id` |
| `EcgSampleOrdinalExtender` | Extend uint32 ECG indices to JSON-safe monotonic ordinals exactly once |
| Serial/BLE readers | Preserve raw chunks first, create `PacketReceipt`, pass `StagedPacket` through coordinator, then dispatch |
| Buffers and analysis | Carry `source_instance_id`, received monotonic time, received UTC time, and ECG ordinal provenance |

This is required so stale packets, resets, replayed packets, and analysis
windows cannot make old physiology look fresh.

## Commands

Install/update PC environment:

```powershell
.\tools\project.ps1 pc-setup
```

Run GUI acquisition owner:

```powershell
.\tools\project.ps1 pc-gui
```

Run MCP server through wrapper:

```powershell
.\tools\project.ps1 pc-health-mcp
```

Read local non-sensitive status:

```powershell
.\tools\project.ps1 pc-health-status
```

Run no-hardware soak:

```powershell
.\tools\project.ps1 pc-health-soak -HealthSoakMinutes 30
```

Run PC tests:

```powershell
.\tools\project.ps1 pc-test
```

For MCP host config, prefer the absolute virtual-environment interpreter under
`pc_app\.venv\Scripts\python.exe`. Equivalent Python 3.12+ interpreters are
acceptable only if the project and exact `mcp==1.28.0` are installed and the
absolute interpreter path is frozen.

## Test Map

Reuse these tests instead of creating new parallel fixtures unless the merge
changes behavior.

| Test file | Coverage |
|---|---|
| `pc_app/tests/test_health_contract.py` | JSON schema, exact four tools, goldens, canonical webhook digest/signature |
| `pc_app/tests/test_health_mcp.py` | Tool envelopes, stale/offline failures, rate limits, official client stdio, JSON-RPC errors |
| `pc_app/tests/test_health_runtime.py` | Worker persistence, disconnect handling, environment validation, device-only snapshots |
| `pc_app/tests/test_health_state.py` | Freshness, quality, clipping windows, counters, analysis provenance, contract validation |
| `pc_app/tests/test_health_store.py` | Event lifecycle, hysteresis, outbox ordering, restart recovery, deletion, observability |
| `pc_app/tests/test_health_webhook.py` | Secret/URL policy, HMAC, receiver validation, idempotency, redirects, retry/dead-letter |
| `pc_app/tests/test_source_coordinator.py` | Reset detection, buffering, voice reset behavior, pending limits, ECG ordinal wrap |
| `pc_app/tests/test_serial_io.py` | Raw chunk preservation, commit gate, pending reset flush |
| `pc_app/tests/test_ble_io.py` | BLE reader integration, voice persistence/ACK with reset candidate handling |
| `pc_app/tests/test_protocol_v0.py` | Shared packet goldens and staged parser accounting |

## Validation Already Completed

The Health MCP branch delivery record in
`docs/plans/2026-07-24-health-mcp-implementation.md` reports:

| Validation | Result |
|---|---|
| PC test suite | 158 passed |
| Synthetic Health soak | 1800.031 seconds |
| State revisions in soak | 3208 |
| MCP calls in soak | 20325 |
| MCP exceptions in soak | 0 |
| Production outbox rows in synthetic soak | 0 |
| SQLite integrity check | `ok` |
| Independent implementation review | PASS |

No firmware flashing, serial/BLE hardware validation, or body-connected
acquisition was performed for the Health MCP branch.

## Merge Checklist

Use this as the minimum merge checklist:

1. Start from the final integration target branch, not from `main` unless the
   goal is only to replay history.
2. Merge or cherry-pick the three Health commits in order:
   `1425d55`, `0f96a8b`, `340c5ea`, then keep `e8373bc` as delivery
   documentation.
3. Keep `docs/specs/*health-mcp*`, `docs/health-mcp.md`, and the Health tests
   intact unless intentionally updating the contract.
4. Resolve `tools/project.ps1` manually so it contains both Health commands and
   the newer microphone/ASR commands from the target branch.
5. In `pc_app/pyproject.toml`, keep `health = ["mcp==1.28.0"]` while preserving
   target-branch extras for mic/ASR.
6. In `gui.py`, preserve HealthRuntimeWorker startup and target-branch mic/ASR
   UI startup. The Health worker should remain optional and env-gated.
7. In `ble_io.py` and `serial_io.py`, keep raw recording before parse decisions,
   `PacketReceipt`, `SourceInstanceCoordinator`, staged packet commit, and
   delayed voice ACK semantics.
8. In `protocol.py`, keep `commit_packet()` and avoid counting parser stats
   before coordinator acceptance.
9. In `buffers.py` and `analysis.py`, keep source/receipt/ordinal provenance.
10. Run `.\tools\project.ps1 pc-test`.
11. Run `.\tools\project.ps1 pc-health-soak -HealthSoakMinutes 30` if the
    integration changed parser, store, runtime, or MCP behavior.
12. Run `git diff --check`.
13. Do not report hardware or body-connected validation unless new actual logs
    are supplied.

## Known Integration Risks

Do not downgrade the microphone branch from `e1814c5` back to the older voice
baseline inside `feat/health-mcp-v0`. The Health branch inherits voice work only
through `abe97de`.

Do not collapse the health webhook into the ordinary Agent webhook. The two
systems have different endpoint paths, payloads, secrets, queues, and safety
meaning.

Do not start the MCP server as the data owner. The data owner is the GUI/runtime
process writing SQLite.

Do not expose delete, raw ECG, cleaned ECG, R peaks, webhook bodies, secrets, or
signatures through MCP.

Do not label synthetic/replay state as live. Synthetic/replay must be
`test_mode=true` and must not enqueue production webhook notifications.

Do not infer medical correctness, robot-motion authorization, or emergency
semantics from Health MCP state or events.

## Safety Boundaries

The Health MCP implementation is an engineering status interface. It is not a
medical device, diagnosis, emergency service, or authorization for robot
movement.

USB debugging is electronics-only without body electrodes. Human ECG
acquisition requires independent battery power and wireless transport. Do not
connect desktop USB, wall power, a charging power bank, or grounded bench
instruments while electrodes are attached to a person.

Firmware flash, flash erase, eFuse changes, serial monitor sessions, and
body-connected acquisition require explicit user instruction.

## Final Handoff Rule

For the large merge, treat this branch as containing already-reviewed,
repository-matched Health MCP code. Prefer adapting call sites and resolving
conflicts over rewriting the feature. If a conflict appears to require changing
contract behavior, update the spec, machine contract, goldens, tests, and
operator docs in the same change.
