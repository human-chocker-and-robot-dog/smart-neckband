# Unified PC Application

## Goal

Make the main Windows GUI the single user-facing smart-neckband application.
It must own sensor acquisition, the unified microphone/ASR/VAD flow, ordinary
Webhook handling, Health state production, and lifecycle control for the
authenticated Health MCP HTTP server.

## Current state

- `python -m smart_neckband` / `pc-gui` starts the unified sensor GUI.
- The main GUI already contains sensor, diagnostics, history, comparison,
  unified microphone, and ordinary Webhook tabs.
- Health state is written only when `SMART_COLLAR_WEARER_ID` exists, but the
  main GUI has no Health/MCP tab or MCP server controls.
- `pc-health-mcp-http` starts the RDK-facing MCP server as a separate command.
- `smart-neckband-mic` / `pc-mic` still exposes a second legacy microphone GUI.
- The unified microphone stops on local VAD `speech_end`, but ASR `final` is
  not yet a stop fallback.

## Scope

Included:

- a testable background process controller for the authenticated Health MCP
  Streamable HTTP server;
- a Health/MCP main-GUI tab with runtime and endpoint status, configuration,
  start/stop controls, token generation, and the exact three MCP tool names;
- automatic Health runtime and MCP startup when environment configuration is
  complete;
- one shared SQLite database and wearer identity for Health runtime and MCP;
- ASR-final fallback stopping in the unified microphone panel;
- removal of standalone GUI launch exposure while retaining compatibility for
  old `smart_neckband.mic_capture_gui` callers;
- tests and operator documentation.

Excluded:

- changing the three MCP tool contracts;
- exposing raw ECG or raw IMU through MCP;
- changing firmware, packet formats, flash, or hardware behavior;
- removing headless MCP/admin/soak commands used for automation and testing.

## Design decisions

- The GUI remains the acquisition and Health-state owner.
- The RDK-facing HTTP MCP runs as a managed child process using the same Python
  environment, wearer ID, and SQLite path. This preserves the reviewed SQLite
  process boundary while presenting one visible Windows application.
- The GUI terminates the managed MCP child during shutdown.
- stdio MCP remains available as a headless compatibility/automation command;
  it is not a second GUI.
- The legacy `mic_capture_gui` module becomes a compatibility redirect to the
  main GUI, so old launch commands no longer open a second application.
- Secrets are masked in the GUI and never written to logs by the controller.

## Work breakdown

1. Add Health integration settings and a managed MCP child-process controller.
2. Add focused lifecycle, validation, and command-construction tests.
3. Add the Health/MCP tab and replace direct `HealthRuntimeWorker` ownership in
   the main window with the unified panel/controller lifecycle.
4. Add ASR-final stop fallback to `UnifiedMicPanel`.
5. Redirect legacy microphone launchers to the main GUI and update wrappers,
   package entry points, and documentation.
6. Run focused tests, complete PC tests, static checks, and review the final
   entry-point inventory.

## Validation

```powershell
.\tools\project.ps1 pc-test
git diff --check
git status --short --branch
```

Focused tests cover settings validation, MCP process start/stop, environment
construction, exact three-tool display, GUI lifecycle wiring, and ASR-final
stop behavior. No firmware build is required because firmware and protocol are
unchanged.

## Risks and rollback

- A child process can survive GUI shutdown if lifecycle handling is wrong.
  Stop, wait, and bounded-kill behavior must be tested.
- Starting Health runtime and MCP with different wearer/database values would
  expose inconsistent data. Both are constructed from one settings object.
- Binding `0.0.0.0` without an allowed-host list or bearer token must fail
  closed.
- Rollback uses ordinary revert commits; do not rewrite main history.

## Progress

- [x] Inventory user-facing PC entry points and identify the missing MCP GUI
  integration.
- [x] Implement Health/MCP controller.
- [x] Integrate Health/MCP tab into the main GUI.
- [x] Retire the standalone microphone GUI entry.
- [x] Complete validation and documentation.
- [x] Merge and push main.

## Discoveries

- The repository has two GUI entry points, but only the main GUI uses the
  unified `CollarC3-*` stream.
- Health MCP code was merged into main, yet remained operationally separate as
  `pc-health-mcp-http`; this is why the feature was invisible in the main app.
- The microphone stop request must be reset for every wake session; otherwise
  idempotence for one ASR/VAD result suppresses `STOP` on the next utterance.
- Reapplying changed MCP settings must restart the child process. Keeping the
  old process alive would leave the GUI showing new values while serving the
  old database, wearer, endpoint, or token.

## Result

The main GUI now owns the visible Health/MCP controls and manages the HTTP MCP
child process for the exact three tools. The former microphone GUI launchers
open the same main application. ASR final and local VAD speech end both stop
the active recording, with one STOP per wake session. The complete PC suite
passed on Windows: `210 passed in 68.67s`. Static diff and PowerShell parser
checks passed. Firmware, flashing, serial monitoring, and hardware behavior
were not changed or validated by this work. Commit `75469f9` was fast-forwarded
to `main` and pushed to the canonical `origin`.
