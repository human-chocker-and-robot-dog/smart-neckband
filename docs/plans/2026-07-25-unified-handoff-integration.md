# Unified Handoff Integration

## Goal

Make `main` contain the reviewed ordinary Agent Webhook, reliable voice bridge,
Health MCP and signed Health Webhook, and the current INMP441/Hi ESP/PC ASR/VAD
functionality described by the three 2026-07-25 handoff documents.

## Current state

- `main` is at `88b841be6f1dc1fdc7f5071769d7a919effbb290`.
- `test/inmp441-ble-capture` is at `e28feb73026cf35d40c7d33e84081f8d93c5a576`.
- `feat/health-mcp-v0` is at `e8656acdad8857d86709bd5dcd2f3c4c42f10421`.
- `docs/webhook-handoff` is at `e44b3773da042e554f44b235c92d77b65edf65a2`.
- The microphone and Health branches share `abe97defef27bda47b6b1a2163e8908eb1df6d6d`.
- The root worktree contains unrelated user changes and is not used for integration.
- The user superseded the classic ESP32 baseline on 2026-07-25. ESP32-C3 is now
  the only production and microphone target; the earlier ESP32-C2 mention was
  corrected by the user.

## Scope

Included:

- Preserve the ordinary durable Agent Webhook and reliable voice ACK bridge.
- Preserve the Health contract, staged packet commit, SQLite store, MCP tools,
  signed Health Webhook, tests, and operator documentation.
- Preserve the microphone experiment firmware, MIC1 parser, Hi ESP wake path,
  PC Volc ASR, threshold VAD, diagnostics, tests, and launcher.
- Resolve dependency and PowerShell wrapper conflicts as semantic unions.
- Archive all three supplied handoff documents in the repository.
- Remove the retired classic ESP32 board profile, SPP backend, build target, and
  active documentation references while preserving historical records where
  they explain already-delivered commits.
- Implement repository-local integration gaps that are required for existing
  software paths to work together, especially durable dispatch of PC ASR final
  text with stable de-duplication.
- Merge the validated integration branch into `main`, commit, and push.

Excluded without separate explicit authorization:

- Flashing or erasing devices, changing eFuses, opening a serial monitor, or
  body-connected acquisition.
- Claims that unperformed hardware concurrency or human-speech checks passed.
- Replacing the production sensor firmware wholesale with the experiment.

## Design decisions

- Merge existing reviewed branches and adapt their call sites; do not rewrite
  Webhook, Health, MCP, ASR, VAD, ADPCM, or packet-accounting modules.
- Keep ordinary Agent and Health Webhook persistence, endpoints, credentials,
  workers, and identifiers independent.
- Keep Health MCP read-only over stdio and sourced from committed SQLite state.
- Keep raw acquisition recording before parse/reset decisions and retain staged
  packet commit plus receipt/source/ordinal provenance.
- Keep microphone audio as MIC1 framing and V0 sensor packets unchanged unless
  a tested dual-protocol owner is introduced.
- Use the existing ordinary `WebhookDispatcher` for PC ASR final text, with a
  deterministic session identity and durable insertion before network submit.
- Keep normal microphone capture from writing WAV unless diagnostic recording
  is explicitly enabled by the user-facing control.

## Work breakdown

1. Merge the microphone branch, Health branch, and Webhook handoff branch while
   preserving history and resolving conflicts manually.
2. Archive the three handoff documents under `docs/handoffs/` and reconcile
   their source-of-truth references with the final integrated tree.
3. Inspect combined PC and firmware ownership boundaries and implement required
   software integration gaps, with focused tests for identity and de-duplication.
4. Run the combined PC test suite and repair integration regressions.
5. Build and size-check applicable firmware targets without flashing hardware.
6. Run formatting/diff checks, review the final scope, commit, push, merge into
   `main`, and push `main`.

## Validation

- `\.\tools\project.ps1 pc-test`
- `\.\tools\project.ps1 pc-health-soak -HealthSoakMinutes 30` when Health,
  parser, source coordination, or store behavior changes materially
- ESP32-C3 production build and size validation
- ESP32-C3 Voice build and size validation
- ESP32-C3 standalone microphone experiment build and size validation
- microphone experiment build/size through `tools/inmp441-test.ps1` if its
  supported non-flashing actions are available
- `git diff --check`
- targeted tests for microphone final-to-Webhook persistence and de-duplication

## Risks and rollback

- The largest merge risks are semantic loss in `pc_app/pyproject.toml`,
  `tools/project.ps1`, transport readers, packet accounting, and GUI lifecycle.
- Firmware integration can exceed ESP32-C3 flash/RAM limits or starve ECG; keep
  experimental microphone firmware separate unless the production integration
  can be built and reviewed without weakening sensor priority.
- Rollback is the normal revert of the integration merge commits. No destructive
  Git history operation, flash operation, or user-worktree cleanup is required.

## Progress

- [x] Read repository instructions and all three supplied handoff documents.
- [x] Create an isolated integration worktree and branch from `main`.
- [x] Merge reviewed branch histories and resolve conflicts.
- [x] Archive and reconcile the handoff documents.
- [x] Implement the required cross-feature integration gaps.
- [x] Run PC validation and the 30-minute Health soak.
- [ ] Firmware build and size validation was stopped at the user's direction;
  `doctor` passed, but no final image was produced.
- [ ] Review, commit, push, merge to `main`, and push `main`.

## Discoveries

- The supplied microphone handoff identifies PC ASR final-to-Webhook dispatch,
  stable instruction identity, a single production BLE owner, and production
  firmware microphone integration as unfinished work rather than existing code.
- The two implementation branches share the ordinary Agent Webhook and reliable
  voice baseline, so those commits should appear only once in the merged graph.
- The root worktree has user changes; an isolated worktree avoids disturbing them.
- A temporary user correction mentioned ESP32-C2, but the final instruction is
  ESP32-C3 only. No C2 migration is in scope.
- The installed ESP-IDF Python environment points to a removed Python 3.12
  installation. A temporary workspace venv restored `doctor`, but managed
  child-process PATH propagation prevented a completed sandbox build. A
  sandbox-external build was started and then stopped by the user before an
  application image was produced.

## Result

The microphone/voice, ordinary Agent Webhook, and Health MCP histories are
combined. The microphone app now embeds the existing Webhook tab, persists PC
ASR final text with deterministic `mic-*` IDs, de-duplicates repeated finals,
and defaults to no WAV capture. The active hardware/build baseline is ESP32-C3
only; the classic board profile, SPP firmware backend, and active classic build
configuration were removed.

PC validation passed with 185 tests. The synthetic Health soak passed after
1800.046 seconds with 3194 state revisions, 20271 MCP calls, zero MCP
exceptions, and zero pending production outbox rows. Firmware build/size was
not completed because the user requested ending further long validation.
