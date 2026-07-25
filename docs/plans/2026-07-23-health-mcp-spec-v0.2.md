# Health MCP v0.2 Contract Plan

## Goal

Replace the context-free Health MCP v0.1 proposal with a detailed, reviewable v0.2 contract that can be implemented from this repository without changing the firmware's raw-data guarantees or pretending that an external robot safety stack already exists.

## Current state

- Firmware emits the transport-independent binary V0 protocol documented in `docs/protocol/v0.md`.
- ECG is sampled at 500 Hz and IMU at 50 Hz. Raw ECG ADC counts remain the primary stream.
- `pc_app` parses UART/SPP/BLE bytes into ECG, IMU, and device-status stores.
- `pc_app/src/smart_neckband/analysis.py` derives cleaned ECG, R peaks, HR, RR, and SQI with NeuroKit2 in a 10-second PC-side window.
- The repository has no Health MCP server, health-event state machine, motion classifier, HRV implementation, robot localization, DimOS client, or physical-action safety gate.
- Pushed `feat/voice-wake-asr` commit `17fc0df` adds inbound voice text/status packets and an outbound voice ACK. Health is not a voice feature, but its reset/reader contract must coexist with those paths.
- The rejection material describes an ordinary Agent instruction/reply webhook from another integration context. A separate `feat/pc-agent-webhook` branch now exists, but that code is not present on or required by this documentation branch; `/v1/instructions` and Agent reply callbacks are not health-event ingress.
- MCP protocol revision `2025-11-25` defines structured tool results, output schemas, tool-execution errors, Streamable HTTP rules, and HTTP authorization behavior.

## Scope

Included:

- A repository-matched architecture and implementation map.
- A local, read-only PC-side Health MCP P0 contract.
- Exact domain definitions, tool inputs, tool outputs, MCP wire examples, and error behavior.
- A separate outbound health-event webhook contract with authentication, replay defense, retries, ordering, and idempotency.
- Event lifecycle, quality calculation, freshness, retention, access control, audit, test vectors, and acceptance criteria.
- A machine-readable contract manifest for schema-driven implementation and contract tests.

Excluded:

- Production code for the MCP server or event publisher.
- Firmware, GPIO, packet, partition, or sample-rate changes.
- Flashing, serial monitoring, or body-connected acquisition.
- Robot movement, DimOS integration, wearer localization, diagnosis, emergency response, or medical validation.

## Design decisions

- Run Health MCP in the Windows PC application/process boundary because that is where parsed raw data and NeuroKit2 results already exist.
- Use stdio as the P0 MCP transport. A loopback-only Streamable HTTP profile is P1; LAN or internet exposure is prohibited until HTTPS and conforming authorization are implemented.
- Keep MCP tools read-only. Remove `APPROACH` and every physical-action mapping from this repository contract.
- Keep webhook delivery independent from the ordinary instruction/reply webhook modules and persist health notifications before delivery attempts.
- Treat webhook bodies as wake-up notifications. MCP state queried after receipt is authoritative.
- Publish only events supported by current data: lead-off/contact loss, sustained ADC clipping, stale input, and offline input. Defer motion, elevated-HR-with-low-motion, HRV, posture, and diagnosis.
- Generate a PC acquisition `source_instance_id` because firmware protocol V0 has no boot ID. Never present it as a device-provided identifier.
- Calculate freshness from the PC monotonic receive clock and invalidate live state after process restart until new packets arrive.

## Work breakdown

1. Inventory the source proposal, rejection reasons, repository code, and official MCP wire rules.
2. Write the v0.2 specification with explicit current-state and target-state labels.
3. Add a machine-readable contract manifest containing exact JSON Schema objects and golden messages.
4. Validate JSON parsing, schema references, complete MCP wire goldens, Markdown hygiene, repository path references, and diff whitespace.
5. Perform an internal rejection-matrix review and correct all findings.
6. Submit the artifacts to an independent review agent; revise and repeat until the reviewer explicitly passes them.
7. Confirm the final diff contains only intended documentation, create a Conventional Commit, and push the branch.

## Validation

```powershell
.\tools\project.ps1 pc-test
git diff --check
git status --short --branch
```

Additional documentation checks:

- Parse the contract manifest as UTF-8 JSON.
- Verify every `$ref` resolves within the manifest.
- Verify every repository path named as an implementation anchor exists.
- Verify P0 tool input and output schemas reject undeclared properties.
- Verify complete golden MCP success/error JSON-RPC messages, structured content, TextContent equality, JSON-RPC error messages, and webhook request/ACK bodies.
- Review every v0.1 rejection point against an explicit v0.2 resolution.

No firmware build is required because this change does not modify firmware, the binary protocol, board configuration, or shared packet vectors.

## Risks and rollback

- A large prose-only document can still drift from implementation. The machine-readable manifest and golden messages are included to make contract tests possible.
- A future MCP SDK may serialize schemas differently. The semantic wire requirements remain normative, while SDK-specific code is non-normative.
- The separate Agent Webhook branch may change independently. This specification assigns distinct health endpoints, event names, persistence, and authentication so the two channels cannot be confused.
- Rollback is deletion of the new specification, contract manifest, and this plan; no runtime data or hardware state is changed.

## Progress

- [x] Read repository instructions and PowerShell execution rules.
- [x] Inspected the source proposal and rejection report.
- [x] Inspected relevant firmware, PC, live-web, and the separate ordinary Agent Webhook branch without making it a dependency.
- [x] Verified the MCP 2025-11-25 tool, transport, and authorization rules.
- [x] Drafted the v0.2 specification.
- [x] Added the machine-readable contract manifest.
- [x] Completed automated documentation validation.
- [x] Completed internal review and fixes.
- [x] Obtained an explicit `PASS` from the independent review agent on the seventh full review.
- [x] Approved the final three-file artifact set for the repository's automatic commit-and-push workflow.

## Discoveries

- The rejection report was written against a different repository and mentions DimOS tools and Agent FIFO behavior that do not exist in `smart-neckband`. The revised contract must define only this repository's producer boundary and consumer requirements, not claim external behavior.
- The current PC analysis exposes HR, latest RR, and mean NeuroKit2 SQI, but not HRV or motion classification.
- Firmware protocol V0 has device monotonic timestamps and packet/sample sequence values but no boot ID. A PC-generated acquisition-source ID is required to make restart boundaries explicit without changing firmware.
- The current live web service transports PC-cleaned ECG and public status for visualization. It is not an authoritative Health MCP backing store.
- The ordinary Agent Webhook branch uses a strict text-instruction/reply contract. Reusing those endpoints for health notifications would violate domain separation and the requested security contract.
- A stdio MCP child cannot share `PcDataStores` memory with an already-running GUI. The P0 design now uses a single acquisition owner that writes an atomic SQLite snapshot and a read-only stdio MCP child that reads it without reopening the COM/BLE device.
- The first `pc-test` run inside the managed sandbox reached pytest but failed with `WinError 5` on the generated base-temp directory. Per the repository's documented sandbox guidance, the identical command was rerun outside the sandbox and all 47 tests passed.
- The first independent review returned `FAIL` with six actionable gaps: raw-body webhook idempotency, stale-to-offline transaction ordering, stale branch-state wording in this plan, a wrong `schemas` pointer, missing complete MCP wire goldens, and an undefined device-timestamp rollback threshold. All six were corrected before requesting re-review.
- The second independent review returned `FAIL` with two remaining ambiguities: interleaved packet handling during a reset candidate and incomplete receiver-side Webhook validation/error mappings. The reset detector now uses one bounded global candidate buffer with deterministic confirm/replay behavior, and the receiver contract now freezes validation order, HTTP status, error code, retryability, Content-Type acceptance, and failure-body semantics.
- The third independent review returned `FAIL` after the repository's concurrent voice-protocol work exposed three integration gaps: voice packet scope during reset buffering, ECG freshness being conflated with generic transport liveness, and an undefined receiver requirement for the Webhook `Accept` header. The contract now buffers every parser-accepted inbound V0 packet while excluding the outbound ACK, separates ECG and transport ages, anchors health freshness/events to ECG only, and makes `Accept` explicitly advisory.
- The fourth independent review returned `FAIL` with eight deeper implementation gaps: coordinator placement before parser stats/voice ACK, missing receipt fields, device-vs-ECG offline semantics, stale analysis provenance, a nullable nested ECG age, undefined per-tool metadata, redirect handling, and receiver deduplication races. The specification and contract now freeze the staged commit pipeline and affected existing files, receipt/provenance fields, `STATE_OFFLINE`, metric validity invariants, per-tool meta mappings/goldens, no-redirect policy, and a unique-index insert-or-read transaction.
- The fifth independent review returned `FAIL` with five repository edge cases: current voice retransmissions use new headers and must remain reset-eligible, ECG sample indices wrap at uint32, the raw recorder must precede staged decode/commit, events cannot reference a state revision before the first ECG state, and the HMAC environment-string encoding was undefined. The contract now matches `17fc0df`, defines a per-source extended ECG ordinal, preserves raw bytes pre-decode exactly once, defers all events until a state revision exists, and accepts only a 64-character lowercase hex secret.
- The sixth independent review returned `FAIL` with one remaining ambiguity: public fields used the expanded ECG ordinal, while the runtime/ring field still looked like the current raw index. The contract now replaces that field with explicit raw and ordinal values, requires serial/BLE/ring/analysis/state to share one post-commit extender, and extends the wrap golden through the second batch's runtime last sample.

## Result

The repository-matched v0.2 specification, machine-readable contract, and implementation plan are complete. The seventh independent full review returned `PASS` with no blocking or important findings. Automated validation covers JSON duplicate keys, all internal references, five complete MCP wire messages, four ToolMeta goldens, positive and negative Schema cases, Webhook SHA-256/HMAC, secret parsing, redirect policy, ECG uint32 wrap through runtime ordinal state, UTF-8, final newlines, and trailing whitespace. The existing PC test suite was also run on the clean documentation branch and passed 47/47; no firmware, flash, monitor, hardware, or body-connected validation was required or claimed for this documentation-only change.
