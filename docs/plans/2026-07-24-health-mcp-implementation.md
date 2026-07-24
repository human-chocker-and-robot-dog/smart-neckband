# Health MCP V0.2 Implementation

## Goal

Implement the repository-matched P0 Health MCP defined by
`docs/specs/Smart_Collar_Health_MCP_Spec_v0.2.md`: a PC-side read-only stdio
MCP server, durable health state and event storage, and a separately queued,
signed health-event webhook. Preserve the existing raw acquisition evidence
path and voice packet behavior.

## Current state

- The implementation branch starts from `feat/voice-wake-asr` commit `abe97de`.
- The reviewed specification and machine contract are present under
  `docs/specs/`.
- The PC application already parses ECG, IMU, device-status, and inbound voice
  packets, stores approximately 30 seconds of samples, and analyzes ECG with
  NeuroKit2.
- `PacketParser.feed()` currently commits sequence/loss statistics before a
  source-reset decision, and serial/BLE readers dispatch decoded packets
  immediately.
- The repository has a separate ordinary Agent webhook implementation. Health
  events must not reuse its endpoint, queue, deduplication keys, or worker.
- The official stable MCP Python SDK `1.28.0` supports protocol revision
  `2025-11-25`; this implementation will pin that exact version.

## Scope

Included:

- Machine-contract loading and executable golden/schema tests.
- Immutable packet receipts, staged parsing, source-reset coordination, and
  per-source ECG uint32-to-ordinal extension shared by serial and BLE.
- Receipt-aware ECG/IMU/status stores and analysis provenance.
- Health state, quality, device-state, event lifecycle, SQLite revisions, audit,
  health webhook outbox, and dead letter storage.
- Four read-only MCP tools over stdio with contract-defined schemas, envelopes,
  errors, metadata, and rate limits.
- Canonical health-event webhook serialization, HMAC signing, URL policy,
  retry/dead-letter behavior, and a mock receiver validator used by tests.
- Operator documentation, synthetic smoke tests, and restart recovery tests.

Excluded:

- Firmware, packet-wire, GPIO, sampling-rate, flash, monitor, and hardware
  changes.
- Human body-connected acquisition.
- HTTP MCP transport, MCP resources/prompts/sampling, HRV, motion/posture
  classification, robot actions, or medical claims.
- Changes to the ordinary Agent instruction/reply webhook contract.

## Design decisions

- Keep exact transport chunks recorded before parsing or reset decisions.
- Split frame decode from packet commit: staged packets do not affect
  `packets_ok`, sequence gaps, or loss until the coordinator commits them.
- Generate one PC-owned `source_instance_id` per reader start or confirmed
  device reset. Buffer every inbound device packet type during a pending reset.
- Generate `PacketReceipt` once after a full valid frame is decoded and preserve
  it across buffering/replay.
- Extend ECG raw indices exactly once after coordinator commit and pass the
  resulting ordinal metadata through runtime, rings, analysis, and health state.
- Use one SQLite database in WAL mode with separate tables for state, events,
  health outbox/dead letters, and MCP audit. State/event/outbox transitions use
  one transaction.
- Treat stored state/event JSON as contract-versioned immutable snapshots.
- Load tool schemas from the checked-in machine contract, resolving local
  `$ref` values before exposing `tools/list`.
- Pin `mcp==1.28.0`; P0 exposes stdio only and writes logs exclusively to
  stderr.
- Serialize webhook bodies once, persist the exact UTF-8 bytes, and recompute
  only timestamp-dependent HMAC headers on retry.

## Work breakdown

1. Add contract/golden tests and pin the official MCP SDK.
2. Refactor protocol parsing into staged decode and explicit commit.
3. Add source coordinator, reset buffering/replay, packet receipts, and ECG
   ordinal extension.
4. Route serial and BLE through the shared commit path while preserving raw
   recording and delayed voice ACK semantics.
5. Add receipt-aware buffers and analysis provenance.
6. Implement health models, quality, state builder, and device snapshot logic.
7. Implement SQLite migrations, state revisions, four event state machines, and
   atomic outbox creation.
8. Implement four stdio MCP tools, rate limits, audit, and JSON-RPC contract
   tests.
9. Implement signed health webhook dispatch, retry/dead letter, and receiver
   contract tests.
10. Document operation, run synthetic/restart/soak validation, review the diff,
    commit coherent changes, and push the feature branch.

## Validation

```powershell
.\tools\project.ps1 pc-test
git diff --check
git status --short --branch
```

Focused tests will also run directly through the repository PC virtual
environment while implementing. The final synthetic smoke test must not flash
firmware, open a serial monitor, or use body electrodes.

## Risks and rollback

- Reset false positives can reorder or hide valid data. Boundary tests cover
  timestamp rollback, sequence wrap, rejected candidates, all inbound packet
  types, and single-commit statistics.
- Receipt or ordinal duplication can make stale analysis appear current. The
  extender has one owner and provenance equality is tested end to end.
- SQLite writer/stdio reader contention can block acquisition. Use short
  transactions, WAL, busy timeout, and independent connections.
- Webhook redirects or invalid secret configuration could leak signed data.
  Fail closed before starting the worker and disable redirects.
- The feature can be rolled back by reverting this branch; it does not alter
  firmware or captured raw-session formats.

## Progress

- [x] Created `feat/health-mcp-v0` from the latest committed voice baseline.
- [x] Imported the reviewed specification and machine contract.
- [x] Added contract fixtures and exact MCP SDK pin.
- [x] Added staged parsing, reset coordination, receipts, and ordinal extension.
- [x] Integrated serial/BLE and analysis provenance.
- [x] Added source-aware health state, quality, event, and SQLite layers.
- [x] Added the four read-only stdio MCP tools and complete wire goldens.
- [x] Added signed health webhook delivery, receiver validation, and recovery.
- [x] Added local non-sensitive runtime observability and administrator status.
- [x] Completed full four-event-cycle synthetic soak diagnostics.
- [x] Passed 158 PC tests and an offscreen GUI/Health runtime smoke test.
- [x] Captured an uninterrupted machine-readable 30-minute synthetic PASS.
- [x] Received final independent-agent PASS.
- [ ] Committed and pushed the reviewed implementation.

## Discoveries

- The shared worktree no longer contains uncommitted voice source changes; only
  the reviewed Health MCP documents were untracked when this branch was created.
- PowerShell 5.1 lacks `ConvertFrom-Json -AsHashtable`; contract inspection uses
  property enumeration instead.
- The official Python SDK stable line is `1.28.0`; v2 is still prerelease and is
  intentionally excluded.
- Editable installation rewrites tracked setuptools `egg-info` metadata; those
  generated diffs are excluded from feature commits.
- Pytest temporary-directory cleanup raises `WinError 5` inside the managed
  Windows sandbox. The same focused suite passes outside the sandbox.
- The first independent review correctly found incomplete exact-wire,
  cross-wearer isolation, device counter/clipping window, and outbox lease
  ordering behavior. Those findings produced regression tests and fixes before
  the second review.
- A clipping window cannot be declared complete from sample count alone.
  Event qualification now requires at least 5000 same-source samples and a full
  ten seconds of immutable receipt-monotonic coverage.
- The 30-minute run survived 3183 state commits and 19983 audited MCP calls;
  its process was terminated by the command host after the timed run, and the
  reopened 2.84 MB SQLite database passed `PRAGMA integrity_check`, recovered
  76 events and 151 contiguous/hash-valid pending outbox rows.
- A separate 75.047-second diagnostic run completed with machine-readable
  `status=passed`, 131 revisions, 807 MCP calls, zero query exceptions, all four
  event families opened/resolved, and eight recovered outbox rows. The runner
  now labels all of its evidence `synthetic`/`test_mode=true` and asserts that
  it creates zero production outbox rows.
- The third independent review found configuration-level wearer IDs were not
  validated and full wearer deletion could retain event hysteresis gates.
  Configuration entry points now enforce the contract pattern; full deletion
  removes gates while deliberately retaining persistent revision/notification
  sequences so identifiers are never reused.
- The final uninterrupted soak completed in 1800.031 seconds with 3208 state
  revisions, 20325 MCP calls, zero MCP exceptions, 100 events, and zero
  production outbox rows. The result reported `status=passed`; an independent
  SQLite check returned `integrity_check=ok`, migration version 2, synthetic
  test-mode state, and resolved episodes for all four event types.

## Result

Implementation and software validation are complete, including an independent
agent PASS. Git handoff remains. No firmware, flash, monitor, serial/BLE
hardware, or body-connected operation was performed.
