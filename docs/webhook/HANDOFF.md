# Smart Neckband Webhook Integration Handoff

> Integration note (2026-07-25): ESP32-C3 is now the only active hardware
> target. Classic ESP32/SPP references below are historical or contract
> compatibility context.

Last verified against repository history on 2026-07-25.

## 1. Purpose

This document is the merge handoff for all Webhook-related work around the Smart Neckband PC application. It is written for an integration Agent that will combine historical branches into one workspace.

The governing implementation rule is:

> Reuse the existing modules, contracts, tests, and commits. Do not rewrite equivalent clients, SQLite schemas, dispatchers, callback receivers, voice assemblers, Health state machines, or MCP tools.

This handoff intentionally contains no copied source code. It identifies the authoritative commits, branches, documents, source files, tests, invariants, and merge order.

## 2. Immediate branch correction

The repository has several related branches, but they are not interchangeable:

| Branch | Purpose | Relationship to Webhook |
| --- | --- | --- |
| `feat/pc-agent-webhook` | Original PC Agent instruction/reply Webhook implementation | Pure Webhook baseline at `c08e696` |
| `feat/voice-wake-asr` | Reliable device voice text transport and offline wake/ASR | Descends from the Webhook baseline and feeds final voice text into the ordinary Agent Webhook queue |
| `feat/health-mcp-v0` | Health state, read-only MCP, and signed Health-event Webhook | Descends from `feat/voice-wake-asr`; adds a second, deliberately independent Webhook system |
| `test/inmp441-ble-capture` | INMP441 capture and PC microphone/ASR experiments | Also descends from `feat/voice-wake-asr`; does not replace the ordinary Webhook modules |
| `docs/webhook-handoff` | This document | Based on the pure Webhook commit so it can be reviewed or cherry-picked independently |

At the time this handoff was prepared, `feat/health-mcp-v0` and `test/inmp441-ble-capture` shared commit `abe97de` and had diverged by 4 Health-only commits and 16 microphone-only commits.

The `docs/webhook-handoff` worktree itself intentionally contains only the pure `c08e696` implementation plus this documentation. Files described below as Health- or voice-branch sources must be reused from the named commits/branches; their absence from this documentation branch is not evidence that they should be reimplemented.

## 3. There are two different Webhook systems

Do not merge these into one endpoint, one queue, one database table family, or one security policy.

### 3.1 Ordinary Agent instruction/reply Webhook

Purpose:

- submit complete user text to an external Agent Gateway;
- receive exactly one final user-visible reply event;
- provide a GUI console for settings, debugging, retries, and reply inspection;
- accept manual text and, on later branches, durable final voice transcripts.

Endpoints:

- outbound instruction: exact path `/v1/instructions`;
- inbound final reply: configurable exact path, default `/agent-replies`.

Local persistence:

- default SQLite path: `data/webhook_client.sqlite3`;
- owns ordinary instruction rows, retry state, settings, and final reply de-duplication.

Security boundary:

- the original MVP contract has no authentication, signature, timestamp validation, or replay protection;
- use loopback or a trusted restricted LAN only;
- do not expose it directly to the public internet.

### 3.2 Health-event Webhook

Purpose:

- wake a trusted consumer when a Health event transitions;
- carry identifiers and transition metadata only;
- require the consumer to query the read-only Health MCP for current state and event details.

Endpoint:

- exact path `/v1/health-events`.

Local persistence:

- Health state, event, outbox, dead-letter, and MCP audit tables live in the Health SQLite database;
- default database is `data/health/health_state.db`, or the path in `SMART_COLLAR_HEALTH_DB_PATH`.

Security boundary:

- canonical UTF-8 request body;
- HMAC-SHA256 over `timestamp + "." + raw_body`;
- independent key ID and 32-byte secret;
- no redirects;
- plain HTTP only for loopback, otherwise HTTPS;
- per-wearer ordering, persistent retries, and dead letters.

The Health Webhook must never reuse the ordinary Agent endpoint, instruction ID, reply ID, retry worker, tables, or credentials.

## 4. Authoritative history and commit order

### 4.1 Ordinary Agent Webhook baseline

Commit:

- `c08e696 feat(pc): add Agent webhook console`

This commit is the coherent implementation unit for:

- settings and contract models;
- durable ordinary instruction storage;
- outbound HTTP submission and automatic retry dispatcher;
- local final-reply callback server;
- Webhook GUI tab;
- integration with the existing PC GUI connection state;
- tests and user documentation.

If the target workspace does not already contain these files, reuse this commit rather than recreating the feature file by file.

### 4.2 Reliable voice text bridge

Commit:

- `17fc0df feat(protocol): add reliable voice text transport`

This commit is a protocol-level unit and must be treated as a set. It adds firmware and PC support for chunked final ASR text, deterministic reassembly, stable voice instruction IDs, and BLE ACK only after durable PC persistence.

It builds directly on `c08e696`.

The later voice baseline is:

- `abe97de feat(voice): add offline wake and streaming ASR`

Use `17fc0df` when only the reliable text bridge is required. Include the later voice branch history when the complete on-device wake/ASR pipeline is required.

### 4.3 Health MCP and signed Health Webhook

Apply or merge the following Health commits in order after the voice baseline:

1. `1425d55 test(pc): add health contract fixtures`
2. `0f96a8b feat(pc): stage health packets before commit`
3. `340c5ea feat(pc): add health MCP runtime`
4. `e8373bc docs(plan): record health MCP delivery`

These four commits are the reviewed Health branch delta. Do not extract only `health_webhook.py`; the implementation depends on the machine contract, staged packet commit, source coordination, provenance-aware buffers, Health state, SQLite transitions, and tests.

### 4.4 Microphone experiment branch

`test/inmp441-ble-capture` contains 16 commits after `abe97de`. These commits add a separate microphone capture GUI, threshold VAD, Volc ASR client behavior, debugging, and an isolated ESP32-C3 microphone experiment.

They are not replacements for the ordinary Webhook implementation. Merge them only if microphone capture/ASR is part of the unified product.

## 5. Ordinary Agent Webhook source map

Reuse these files as the implementation boundaries.

| File | Responsibility |
| --- | --- |
| `pc_app/src/smart_neckband/webhook_models.py` | Endpoint validation, settings, instruction states, reply event validation, body-size constants, device-receiving gate |
| `pc_app/src/smart_neckband/webhook_store.py` | SQLite initialization, settings, durable instruction lifecycle, retry scheduling state, final-reply de-duplication, crash recovery |
| `pc_app/src/smart_neckband/webhook_client.py` | Strict instruction POST, HTTP/error classification, exponential retry dispatcher, background worker lifecycle |
| `pc_app/src/smart_neckband/webhook_receiver.py` | Local exact-path HTTP callback server, request validation, persist-before-ACK behavior, duplicate/conflict handling |
| `pc_app/src/smart_neckband/webhook_ui.py` | Dedicated GUI tab, settings controls, manual instruction submission, stop shortcut, records, callback listener controls, DEBUG output |
| `pc_app/src/smart_neckband/gui.py` | Creates the Webhook tab and passes current `ConnectionState.RECEIVING` into the ordinary-send gate |
| `pc_app/tests/test_webhook.py` | Executable contract for persistence, strict fields, HTTP classification, retry ID stability, reply de-duplication, callback ACK, and device gate |
| `docs/pc-agent-webhook.md` | Operator guide for local/LAN deployment and troubleshooting |
| `docs/plans/2026-07-23-pc-agent-webhook.md` | Design decisions, scope, risks, and original delivery record |

Do not move SQLite or network work into the Qt main thread. The existing code already separates worker threads and uses the GUI callback queue.

## 6. Ordinary Agent Gateway contract

The original external handoff document is:

- source name: `agent-input-webhook-integration.md`;
- original local path: `C:\Users\XWen1024\Downloads\agent-input-webhook-integration.md`;
- document size: 39,021 bytes;
- last modified: 2026-07-23 15:46:02 +08:00;
- SHA-256: `5E5A4E94ADFBB857A71BDA0F27F889F15017F9072544AF965EFB21A1ED54BFFF`.

When that document is supplied in the merge packet, treat it as the authoritative external Gateway contract. The Smart Neckband repository contains the PC client and callback receiver, not the Gateway implementation described as `integrations/agent-webhook-gateway`.

The integration Agent must preserve these rules:

- the instruction request body contains exactly `instruction_id` and `text`;
- both are strings and must be non-empty after trimming for validation;
- the original text is stored and resent without rewriting;
- the whole JSON body is limited to 64 KiB;
- path is exactly `/v1/instructions`, with no trailing slash, query, or fragment;
- a new instruction is persisted locally before the first network attempt;
- retries reuse the exact same instruction ID and exact same text;
- HTTP 202 means accepted into the Gateway inbox, not Agent completion or physical action;
- network errors, timeouts, and HTTP 503 are retryable;
- HTTP 400, 404, and 409 are contract/configuration failures and are not silently converted into new IDs;
- a final callback event is `agent.reply.completed` with `reply_id`, `instruction_id`, `text`, and `completed_at`;
- the callback receiver persists before returning success;
- an identical duplicate `reply_id` is acknowledged without duplicate UI/TTS/side effects;
- callback order is not assumed to match instruction order;
- the Gateway reply URL is deployment-wide and read at Gateway startup;
- changing the PC UI field does not reconfigure an already-running Gateway;
- the fixed failure text is still delivered as a normal final reply event;
- the text `停` is a Gateway fast path, not proof of physical stop and not a physical emergency-stop mechanism.

## 7. Voice-to-Webhook bridge

The reliable voice bridge is not a second instruction client. It adapts device final text into the existing `WebhookDispatcher`.

Source locations added or modified by `17fc0df`:

| File | Responsibility |
| --- | --- |
| `firmware/main/voice_link.c` and `voice_link.h` | Device-side final-text retention/retransmission and ACK processing |
| `firmware/main/protocol_v0.c` and `protocol_v0.h` | Voice text chunk, status, and ACK packet definitions |
| `firmware/main/ble_transport.c` | BLE bidirectional data/control transport for ACK |
| `firmware/main/spp_transport.c` | Corresponding transport support where applicable |
| `firmware/main/packet_task.c` and `packet_task.h` | Packet scheduling and voice packet ownership |
| `pc_app/src/smart_neckband/protocol.py` | Voice packet decode/encode and constants |
| `pc_app/src/smart_neckband/voice.py` | UTF-8 chunk assembler, duplicate/conflict handling, stable transcript object |
| `pc_app/src/smart_neckband/ble_io.py` | Reassembly, durable callback, and queued BLE control ACK writes |
| `pc_app/src/smart_neckband/webhook_ui.py` | `enqueue_voice_text` adapter and voice status display |
| `pc_app/src/smart_neckband/gui.py` | Connects BLE voice callbacks to the Webhook tab |
| `docs/protocol/v0.md` and `docs/protocol/v0_golden_vectors.json` | Wire contract and golden vectors |
| `docs/plans/2026-07-23-voice-wake-asr.md` | Voice architecture and validation plan |
| `pc_app/tests/test_voice.py`, `test_protocol_v0.py`, `test_ble_io.py` | Reassembly, wire, duplicate, ACK, and transport coverage |

Non-negotiable durability sequence:

1. reassemble a complete final UTF-8 transcript;
2. derive stable instruction ID `voice-<16-lowercase-hex-utterance-id>`;
3. persist it through the existing ordinary `WebhookStore`/dispatcher;
4. return success to the BLE reader;
5. only then send the device ACK.

If persistence fails, no ACK is sent. Device retransmission must remain safe because identical voice instruction IDs and text are idempotent.

Do not create a parallel voice-only Webhook queue.

## 8. Health MCP and Health Webhook source map

The Health implementation is contract-driven. Reuse the full layer set.

### 8.1 Normative documents

| File | Role |
| --- | --- |
| `docs/specs/Smart_Collar_Health_MCP_Spec_v0.2.md` | Human-readable normative Health/MCP/Webhook specification |
| `docs/specs/health-mcp-v0.2.contract.json` | Machine-readable schemas, tool definitions, Webhook contract, and golden messages |
| `docs/health-mcp.md` | Operator and deployment guide |
| `docs/plans/2026-07-23-health-mcp-spec-v0.2.md` | Specification review history |
| `docs/plans/2026-07-24-health-mcp-implementation.md` | Implementation decisions, validation, and discoveries |

### 8.2 Acquisition and provenance

| File | Responsibility |
| --- | --- |
| `pc_app/src/smart_neckband/source_coordinator.py` | Staged packet commit, reset detection, buffering/replay, packet receipts, ECG ordinal extension |
| `pc_app/src/smart_neckband/protocol.py` | Staged decode and explicit packet commit behavior |
| `pc_app/src/smart_neckband/serial_io.py` and `ble_io.py` | Shared coordinated packet commit path and runtime provenance |
| `pc_app/src/smart_neckband/buffers.py` | Receipt/source-aware ECG, IMU, and status storage |
| `pc_app/src/smart_neckband/analysis.py` | Analysis provenance linked to committed evidence |

### 8.3 Health domain and persistence

| File | Responsibility |
| --- | --- |
| `pc_app/src/smart_neckband/health_contract.py` | Loads the frozen machine contract, validates wearer IDs, resolves schemas and tool contracts |
| `pc_app/src/smart_neckband/health_quality.py` | Ten-second quality windows, clipping completeness, parser/device counter deltas |
| `pc_app/src/smart_neckband/health_state.py` | Device snapshot, freshness, heart metrics availability, Health state builder |
| `pc_app/src/smart_neckband/health_store.py` | SQLite migrations, state revisions, event lifecycles, atomic outbox, leases, retry/dead-letter, retention, deletion, audit |
| `pc_app/src/smart_neckband/health_runtime.py` | 500 ms worker that builds and commits device/Health state from the PC acquisition owner |
| `pc_app/src/smart_neckband/health_admin.py` | Local status, exact deletion plan, confirmation-token deletion |
| `pc_app/src/smart_neckband/health_soak.py` | Synthetic long-run state/MCP/store recovery validation |

### 8.4 Health Webhook and MCP

| File | Responsibility |
| --- | --- |
| `pc_app/src/smart_neckband/health_webhook.py` | Canonical body, HMAC, URL policy, no-redirect client, independent dispatcher, receiver validator/store |
| `pc_app/src/smart_neckband/health_mcp.py` | Exactly four read-only stdio MCP tools, schemas, rate limits, result envelopes, audit |
| `pc_app/tests/test_health_webhook.py` | Signatures, receiver order, dedup/conflict, redirect rejection, retries, sequencing, restart, dead letters, queue independence |
| `pc_app/tests/test_health_mcp.py` | Exact tools, schemas, stdio SDK interoperability, domain errors, rate limits, wire behavior |

## 9. Health Webhook invariants

Preserve all of the following:

- exact endpoint `/v1/health-events`;
- separate Health outbox and dispatcher;
- secret is exactly 32 random bytes encoded as 64 lowercase hexadecimal characters;
- do not accept uppercase, whitespace, `0x`, Base64, passphrases, or partial configuration;
- use canonical persisted UTF-8 body bytes for all attempts;
- recompute only timestamp-dependent signature headers on retry;
- disable redirects for every redirect status;
- preserve per-wearer `notification_sequence` ordering;
- retry retryable network/HTTP failures with bounded jittered backoff;
- pause automatic delivery after HTTP 401 or 403;
- move terminal or 24-hour failures to a dead-letter table;
- receiver persists and deduplicates before returning HTTP 202;
- a notification is only a wake-up signal; query MCP for details;
- notifications do not contain raw/clean ECG arrays, R peaks, HR, RR, SQI, diagnostic text, or secrets;
- a blocked ordinary Agent instruction queue must not block the Health queue;
- no Health notification may directly authorize robot movement.

## 10. Health MCP invariants

The Health MCP implementation exposes exactly four read-only tools:

- `health.get_current_state`
- `health.get_event_details`
- `health.get_recent_events`
- `health.get_device_status`

Source of truth is the machine contract, not handwritten duplicated schemas.

Preserve these boundaries:

- stdio transport only for P0;
- official stable Python SDK `mcp==1.28.0`;
- MCP protocol revision `2025-11-25`;
- no MCP network port, resources, prompts, or sampling;
- no raw or cleaned waveform arrays returned;
- tools are read-only, idempotent, and closed-world;
- operational logs go to stderr, not MCP stdout;
- stale/offline evidence never becomes fresh after process restart without new input;
- invalid or mismatched wearer IDs fail closed.

## 11. Data ownership and database separation

| Data | Owner | Default location |
| --- | --- | --- |
| Ordinary Agent instruction/reply state | `WebhookStore` | `data/webhook_client.sqlite3` |
| Health state/events/outbox/audit | `HealthStore` | `data/health/health_state.db` |
| Raw acquisition chunks | Existing recorder/session pipeline | `data/` and session-specific raw files |
| External Agent Gateway inbox/outbox/session | External Gateway implementation | Outside this repository |

Never migrate Health rows into `webhook_client.sqlite3`. Never use an ordinary Agent `instruction_id` or `reply_id` as a Health `notification_id` or event ID.

Raw ECG preservation rules remain higher priority than either Webhook. Webhooks must not mutate, filter, replace, or delete primary raw acquisition evidence.

## 12. Configuration map

### 12.1 Ordinary Agent Webhook

Configuration is stored by the PC Webhook tab and includes:

- Gateway URL;
- callback bind host, port, and exact path;
- Gateway-accessible callback URL;
- request timeout;
- require-device-receiving gate;
- callback auto-start.

The external Gateway must separately receive `AGENT_WEBHOOK_REPLY_URL` before Gateway startup.

### 12.2 Health runtime and MCP

Required for Health state/MCP:

- `SMART_COLLAR_WEARER_ID`;
- optional `SMART_COLLAR_HEALTH_DB_PATH`.

All-or-nothing optional Health Webhook configuration:

- `SMART_COLLAR_HEALTH_WEBHOOK_URL`;
- `SMART_COLLAR_HEALTH_WEBHOOK_KEY_ID`;
- `SMART_COLLAR_HEALTH_WEBHOOK_SECRET_HEX`.

Do not commit secrets or local databases.

## 13. Threading and lifecycle ownership

The integration Agent must preserve the existing concurrency model:

- Qt main thread owns widgets only;
- BLE/serial readers own transport reads and packet staging;
- ordinary `WebhookDispatcher` owns blocking instruction HTTP attempts;
- ordinary callback receiver owns its local HTTP server thread;
- voice assembler runs in the receive path but only ACKs after the durable callback succeeds;
- `HealthRuntimeWorker` evaluates state every 500 ms on its own thread;
- `HealthWebhookDispatcher` has an independent worker and database lease/order rules;
- MCP is a separate stdio process reading committed SQLite state;
- SQLite uses short transactions, WAL, busy timeouts, and independent connections.

Do not make Webhook delivery, MCP calls, or SQLite migrations block ECG sampling or the Qt rendering loop.

## 14. Merge strategy for the historical consolidation

### 14.1 Recommended order

1. Establish the desired common baseline containing `c08e696`.
2. Bring in `17fc0df` as a single protocol/voice bridge unit if device voice text is required.
3. Bring in `abe97de` and any selected later voice commits if full wake/ASR is required.
4. Bring in the four `feat/health-mcp-v0` commits in their original order if Health MCP/signed notifications are required.
5. Bring in the 16 microphone experiment commits only if that separate capture application is required.
6. Resolve dependency and wrapper unions manually, then run the full combined validation matrix.

### 14.2 Do not cherry-pick isolated implementation files

Avoid these partial integrations:

- `health_webhook.py` without `health_store.py`, the contract, and tests;
- `webhook_ui.py` without models/store/client/receiver;
- voice firmware packets without PC protocol/goldens/ACK path;
- Health MCP without staged packet commit and provenance;
- microphone GUI while dropping its `websocket-client` dependency and launcher action.

### 14.3 Known both-modified hotspots

A merge-tree comparison between `feat/health-mcp-v0` and `test/inmp441-ble-capture`, using common base `abe97de`, reports both branches modified:

- `pc_app/pyproject.toml`
- `tools/project.ps1`

Even if Git can textually auto-merge parts of them, perform a manual semantic union.

For `pc_app/pyproject.toml`, preserve:

- the Health extra with exact `mcp==1.28.0`;
- microphone/GUI `websocket-client` dependency;
- the microphone console script if that app is retained;
- the agreed Python minimum after checking all deployment machines.

For `tools/project.ps1`, preserve:

- existing build/flash/PC actions;
- `pc-health-mcp`;
- `pc-health-status`;
- `pc-health-soak` and its parameters;
- `pc-mic`;
- the robust PC virtual-environment selection/repair logic;
- `pc-setup` installing both ordinary GUI/serial dependencies and the Health extra.

Do not resolve these files by choosing one side wholesale.

## 15. Verification evidence and tests to reuse

### 15.1 Ordinary Agent Webhook

The original delivery passed 57 PC tests, including 10 Webhook tests, plus an offscreen PySide6 construction/close smoke test.

Primary test file:

- `pc_app/tests/test_webhook.py`.

### 15.2 Reliable voice bridge

Primary tests:

- `pc_app/tests/test_voice.py`;
- voice cases in `test_protocol_v0.py`;
- BLE callback/ACK cases in `test_ble_io.py`;
- shared golden vectors in `docs/protocol/v0_golden_vectors.json`.

### 15.3 Health MCP and Health Webhook

The Health branch recorded:

- 158 passing PC tests;
- offscreen GUI/Health runtime smoke;
- an uninterrupted 30-minute synthetic soak;
- 3,208 state revisions;
- 20,325 MCP calls;
- zero MCP exceptions;
- all four event families exercised;
- zero production outbox rows from synthetic/test-mode evidence;
- SQLite integrity check passing after reopen;
- independent-agent PASS.

Primary tests:

- `test_health_contract.py`;
- `test_source_coordinator.py`;
- `test_health_state.py`;
- `test_health_store.py`;
- `test_health_webhook.py`;
- `test_health_mcp.py`;
- `test_health_runtime.py`.

Do not replace these tests with newly invented simplified tests. Carry them forward and make the unified branch pass them.

## 16. Required validation after the big merge

At minimum:

1. run the full PC test suite through `tools/project.ps1`;
2. run `git diff --check`;
3. verify the ordinary Webhook local callback flow with duplicate reply delivery;
4. verify ordinary retries keep the same ID and exact text;
5. verify voice final text is persisted before ACK and duplicates are idempotent;
6. validate the Health machine contract and golden signatures;
7. run Health MCP through the official stdio client test;
8. verify ordinary and Health queues remain independent under blockage;
9. run the synthetic Health soak before claiming long-run stability;
10. if `17fc0df` or later voice firmware is present, build and size-check ESP32-C3 firmware and validate shared protocol goldens;
11. only flash or monitor hardware when separately and explicitly authorized;
12. do not claim body-connected validation without battery-powered wireless evidence.

## 17. Known limitations and deferred work

### Ordinary Agent Webhook

- no built-in authentication or signatures;
- no Gateway health/readiness endpoint;
- no status-query, cancellation, or replay API;
- callback URL is deployment-wide and startup-only;
- no streaming Agent response;
- no automatic TTS delivery in the Webhook tab;
- final reply text is not written back to the collar/OLED;
- `停` is not a physical emergency stop.

### Voice bridge

- the pure `c08e696` Webhook branch does not contain device voice packets;
- voice support begins at `17fc0df` and includes firmware/protocol changes;
- later INMP441 capture work is experimental and separate from the main collar transport.

### Health MCP/Webhook

- P0 is read-only stdio MCP;
- no HTTP MCP transport;
- no diagnosis, emergency service, motion authorization, or raw waveform export;
- Health runtime requires committed ECG evidence before wearer state becomes available;
- Health webhook delivery must remain disabled on partial/invalid secret configuration.

## 18. Recommended handoff packet and reading order

Give the integration Agent these documents together:

1. `docs/webhook/HANDOFF.md` — this merge map;
2. external `agent-input-webhook-integration.md` — authoritative Agent Gateway contract;
3. `docs/pc-agent-webhook.md` — ordinary PC operation;
4. `docs/plans/2026-07-23-pc-agent-webhook.md` — ordinary design record;
5. `docs/protocol/v0.md` and `docs/protocol/v0_golden_vectors.json` — shared wire contract when voice is included;
6. `docs/plans/2026-07-23-voice-wake-asr.md` — voice bridge context;
7. `docs/specs/Smart_Collar_Health_MCP_Spec_v0.2.md` — normative Health specification;
8. `docs/specs/health-mcp-v0.2.contract.json` — machine contract and goldens;
9. `docs/health-mcp.md` — Health operator guide;
10. `docs/plans/2026-07-24-health-mcp-implementation.md` — Health implementation and validation record.

Read the contracts before editing implementation files.

## 19. Definition of successful consolidation

The historical merge is successful only when:

- the existing ordinary Agent Webhook modules are reused intact or evolved in place;
- manual text and device final voice text share one durable ordinary instruction queue;
- device voice ACK still occurs only after durable local persistence;
- ordinary final replies remain persist-before-ACK and `reply_id`-deduplicated;
- Health event delivery remains a separate signed ordered outbox;
- Health consumers query the four read-only MCP tools rather than treating notification payloads as complete state;
- the machine contract and golden vectors remain authoritative;
- the unified dependency and PowerShell wrapper files preserve both Health and microphone capabilities where selected;
- all carried-forward tests pass;
- no secrets, databases, captures, raw ECG, or generated build output enter Git;
- no new duplicate implementation is introduced where a repository module already provides the behavior.

## 20. Final instruction to the merge Agent

Before writing new code, search the source map and branch commits in this document. If equivalent behavior already exists, merge, import, adapt, or wrap it. New implementation is justified only when the required behavior is absent from all referenced branches and contracts, and that gap is documented explicitly.
