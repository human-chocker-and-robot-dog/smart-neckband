# Health Event Agent Bridge V0.3

## Goal

Integrate the reviewed Health MCP V0.2 implementation into the current
microphone/ASR branch, then evolve the PC-side health integration into a
push-plus-pull design:

- the Windows PC sends signed health-event Webhooks when configurable local
  rules trigger;
- the RDK Agent receives only the latest trigger snapshot;
- the Agent can query the authoritative recent window through exactly three
  read-only MCP tools for heart rate, HRV, and IMU motion state;
- the same three tools are available over local stdio and authenticated
  Streamable HTTP for the RDK deployment.

## Current state

- The implementation branch starts from `test/inmp441-ble-capture` at
  `e28feb7` and is named `feat/health-event-bridge-v0.3`.
- User-owned worktree changes already existed before this work: `HANDOFF.md`
  is deleted and `MCP HANDOFF.md` plus `麦克风 HANDOFF.md` are untracked. They
  must remain untouched.
- The reviewed Health MCP baseline is `feat/health-mcp-v0` at `e8373bc`.
  It contains staged packet handling, source reset coordination, receipt-aware
  buffers, Health SQLite state/events/outbox, four read-only stdio MCP tools,
  signed health Webhooks, tests, and a completed synthetic soak.
- The current integration proposal is
  `health-event-agent-bridge-v0.3-integration.md`. Its original direct-through
  decision said that the Webhook was the complete Agent input and prohibited a
  Health MCP follow-up. The product decision has changed: the Webhook is a
  trigger snapshot and the Agent queries the recent authoritative window by
  MCP.
- The Agent will run on an RDK board. The Windows acquisition PC owns the
  sensor buffers, NeuroKit2 analysis, derived metric history, and SQLite
  database, so the Windows process must host the remote MCP server and the RDK
  acts as its client.
- Firmware, sampling rates, GPIO assignments, packet wire format, and raw ECG
  preservation are unchanged.

## Scope

Included:

- Merge the reviewed Health MCP V0.2 implementation without rolling back the
  newer microphone, ASR, BLE, GUI, or voice-ACK behavior on the current branch.
- Expose exactly these MCP tools:
  `health.get_heart_rate`, `health.get_hrv`, and `health.get_imu_state`.
- Add recent derived-metric history sufficient for bounded queries, defaulting
  to the latest 30 seconds without exposing raw ECG, cleaned ECG, R peaks, or
  raw IMU arrays.
- Define HRV V0.3 around time-domain metrics with RMSSD as the primary rule
  value; include validity, quality, and sample-count metadata.
- Define a transparent 0-100 IMU motion score from acceleration and gyro
  activity, plus still-time percentage and quality/coverage metadata.
- Keep stdio MCP for local validation and add authenticated Streamable HTTP for
  the RDK client.
- Add a configurable local health-rule engine and signed
  `POST /v1/health-events` delivery with durable outbox, notification
  idempotency, event-revision idempotency, lifecycle, cooldown, and quality
  gates.
- Keep ordinary ASR/user text on `POST /v1/instructions`; do not merge its
  queue or idempotency keys with health events.
- Add contract files, event catalog, golden UTF-8 bodies/digests/signatures,
  tests, RDK configuration guidance, and Windows firewall guidance.

Excluded:

- Firmware, flash, serial monitor, eFuse, partition, or body-connected work.
- Medical diagnosis, emergency classification, or authorization of physical
  robot movement.
- Threshold changes through MCP; rule thresholds remain local configuration.
- Raw ECG/IMU export through MCP or Webhook.
- Public-internet exposure of the MCP server.

## Design decisions

- The Health MCP server remains colocated with the authoritative Windows data
  and SQLite database. The RDK initiates MCP requests to Windows.
- MCP business-tool count is exactly three. Freshness, data source, signal
  quality, lead-off, clipping, IMU-online state, and provenance are embedded in
  tool results instead of becoming additional tools.
- All three tools accept an optional `window_s`, default 30 seconds, with a
  bounded contract range. The server is configured for one pseudonymous wearer
  and the caller does not select arbitrary wearer IDs.
- Heart-rate responses contain latest and aggregate BPM plus a bounded derived
  trend series. HRV responses contain RMSSD, SDNN, pNN50, mean NN, valid NN
  count, and explicit validity/confidence. A 30-second result is labelled an
  ultra-short engineering estimate.
- IMU samples are summarized locally. Per-second activity combines
  gravity-removed acceleration activity and gyro magnitude; the 30-second
  response reports a 0-100 motion score, still ratio, level, coverage, and a
  bounded per-second trend. Reference normalization values remain configurable
  and must be calibrated with static, ordinary-motion, and vigorous-motion
  captures.
- Health Webhooks contain only the latest trigger snapshot and a recommended
  query window, not the recent metric arrays. The abstract capability
  `health.inspect_recent_metrics` tells the Agent policy to call the three MCP
  tools; the collar does not send MCP tool names or arguments as commands.
- Trigger evaluation uses duration, minimum quality, lead-off/clipping gates,
  lifecycle state, and cooldown so a single noisy R peak cannot create an
  action opportunity.
- The existing Health MCP V0.2 state contract remains available internally
  during migration where needed, but the Health Event Bridge gets a separate
  V0.3 machine contract. Webhook version ownership is separated from the MCP
  state-contract constant.
- Streamable HTTP uses bearer authentication. Initial demo use is restricted
  to an isolated LAN with the Windows firewall limited to the RDK address.
  Production use requires HTTPS or a private encrypted overlay such as
  Tailscale/WireGuard.

## Work breakdown

1. Merge `feat/health-mcp-v0` into the current integration branch and resolve
   shared-file conflicts while preserving newer microphone/ASR behavior.
2. Run the merged baseline tests before changing the public contracts.
3. Split the event-bridge contract from the Health MCP V0.2 contract and add
   the V0.3 schemas, event catalog, and goldens.
4. Add bounded derived metric history and implement the three MCP services.
5. Replace the four-tool MCP registration with exactly the three product tools
   while keeping internal administrator/status functionality outside MCP.
6. Add authenticated Streamable HTTP transport and document Windows/RDK
   configuration, network direction, timeouts, and firewall scope.
7. Add the IMU motion scorer and focused calibration fixtures.
8. Add configurable alert rules for low-motion/high-HR,
   low-motion/HRV-threshold, and high-motion/extreme-HR scenarios.
9. Upgrade the health Webhook envelope, outbox, receiver reference, two-level
   idempotency, and Agent capability hints.
10. Run focused tests, the complete PC suite, contract/golden checks, synthetic
    integration tests, and a bounded soak; update this plan with discoveries.
11. Inspect the final diff, update operator/integration documentation, create
    coherent Conventional Commits, and push the branch to `origin`.

## Validation

Required final validation:

```powershell
.\tools\project.ps1 pc-test
.\tools\project.ps1 pc-health-soak -HealthSoakMinutes 5
git diff --check
git status --short --branch
```

The user has capped Health MCP soak validation at five minutes. Longer runs
must not be started unless explicitly requested. A run stopped after reaching
that window is recorded as user-terminated, not as a product failure, and its
partial database is preserved.

Focused validation will cover:

- exact three-tool MCP registration and schemas;
- stdio and authenticated Streamable HTTP calls;
- 30-second HR, HRV, and IMU response windows;
- HRV insufficient-data and poor-quality behavior;
- deterministic IMU score fixtures and score bounds;
- HMAC canonical raw-body golden vectors;
- unknown event/evidence preservation;
- notification and `(event_id, event_revision)` idempotency;
- concurrent duplicate delivery;
- replay/synthetic/test-mode robot-action gates;
- trigger duration, hysteresis, cooldown, and restart recovery;
- preservation of microphone, ASR, BLE, voice, parser, and source-reset tests.

No firmware build is required unless implementation unexpectedly changes
firmware or the shared binary protocol. No flash, monitor, or hardware test is
authorized by this plan.

## Risks and rollback

- Merging the Health branch can silently roll back newer microphone/ASR code.
  Review every shared auto-merge and retain target-branch behavior around the
  Health source-coordination hooks.
- A 30-second HRV estimate can be unstable or unavailable. Return explicit
  validity and evidence counts; do not synthesize values or diagnosis.
- Motion scores depend on sensor range, mounting, and calibration. Keep the
  formula transparent, configurable, bounded, and covered by deterministic
  fixtures.
- LAN bearer tokens are exposed without transport encryption. Restrict the
  demo network and firewall; require TLS or a private encrypted overlay for
  production.
- Duplicate Webhooks or Agent retries could repeat robot behavior. Maintain
  notification and event-revision idempotency and leave physical-action
  deduplication/safety enforcement on the RDK side.
- Rollback is performed by reverting coherent feature commits. Do not rewrite
  history or use destructive Git commands.

## Progress

- [x] Read the integration proposal and reviewed Health MCP handoff.
- [x] Confirmed the push-plus-pull product decision and exactly three MCP tools.
- [x] Confirmed the RDK deployment requires remote MCP access to Windows data.
- [x] Created `feat/health-event-bridge-v0.3`.
- [x] Merge and validate the Health MCP V0.2 baseline.
- [x] Add the V0.3 bridge contract and event catalog.
- [x] Implement derived metric history and the three MCP tools.
- [x] Implement authenticated Streamable HTTP transport.
- [x] Implement motion scoring, alert rules, and V0.3 health Webhooks.
- [x] Complete the full PC suite, contract checks, HTTP client integration,
  and bounded soak validation.
- [ ] Inspect the final diff, commit the intended feature files, and push.

## Discoveries

- The pre-merge worktree contains user-owned HANDOFF changes. They are outside
  this feature and must remain untouched.
- A read-only merge-tree check identified content conflicts in
  `pc_app/pyproject.toml` and `tools/project.ps1`; shared acquisition files also
  require semantic review even when Git auto-merges them.
- The actual merge produced one textual conflict in `tools/project.ps1`.
  Resolution retained `pc-mic` together with `pc-health-mcp`,
  `pc-health-status`, and `pc-health-soak`; `pc_app/pyproject.toml` auto-merged
  the microphone entry point, Python 3.11 compatibility, and the Health MCP
  dependency.
- The post-merge baseline completed `181 passed` in 50.36 seconds on Python
  3.11.13. This validates the integrated Health, BLE, microphone, ASR, voice,
  parser, source-coordinator, and ordinary Webhook behavior before the V0.3
  contract changes.
- The original V0.3 proposal removed MCP follow-up queries primarily to reduce
  cross-machine integration complexity. The chosen RDK deployment restores
  authoritative pull queries and therefore requires a Windows-hosted remote
  MCP transport.
- The public MCP surface is now exactly three tools:
  `health.get_heart_rate`, `health.get_hrv`, and `health.get_imu_state`.
- The complete PC suite passes with `190 passed`. The official Streamable HTTP
  client also completed authenticated calls successfully.
- A corrected one-minute diagnostic soak completed in 60.062 seconds with 107
  revisions, 666 MCP calls, zero MCP exceptions, and 107 metric-history rows.
- A subsequent long soak remained stable beyond the new five-minute
  acceptance window and was stopped at the user's request. It is recorded as
  user-terminated, not failed. Its partial database remains at
  `data/health/v03-30min-soak.sqlite3` for inspection.
- The user changed the Health MCP soak policy from 30 minutes to a maximum of
  five minutes for future validation; the wrapper default and current
  documentation now enforce that bound.

## Result

The V0.3 Health Event Agent Bridge implementation and software validation are
complete. Exactly three Health MCP tools are available over stdio and
authenticated Streamable HTTP; signed Health Webhooks carry only the trigger
snapshot and direct the Agent to inspect the recent metric window. The full PC
suite, contract checks, official HTTP client integration, and bounded soak
validation passed. No firmware, flash, monitor, hardware, or body-connected
validation was performed or required.
