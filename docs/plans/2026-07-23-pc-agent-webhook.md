# PC Agent Webhook Integration

## Goal

Add a dedicated Webhook tab to the Windows PC application. The tab submits stable user-text instructions to the Agent Webhook Gateway, receives and de-duplicates final reply callbacks, persists client-side state, and can require a live ESP32-C3 data connection before ordinary instructions are sent.

## Current state

- The PySide6 GUI lives primarily in `pc_app/src/smart_neckband/gui.py` and currently has Live, Diagnostics, History, and Comparison tabs.
- Serial and BLE readers expose a common runtime status and feed ECG, IMU, and device-status stores.
- The device protocol has no user-text or Webhook packet type. Webhook traffic is therefore PC-side HTTP traffic, not BLE traffic.
- The Gateway contract accepts strict `POST /v1/instructions` JSON with only `instruction_id` and `text`, then asynchronously posts `agent.reply.completed` events to a deployment-level callback URL.
- The Gateway has no health endpoint or runtime configuration API. Its reply URL is configured at Gateway startup.

## Scope

Included:

- PC-side outbound instruction persistence and HTTP submission.
- Correct retry classification for network errors, timeouts, and HTTP 503.
- A local HTTP callback receiver with durable `reply_id` de-duplication.
- A Webhook GUI tab for settings, instruction testing, state, replies, and debug logs.
- Optional gating of ordinary instruction submission on the existing `RECEIVING` connection state.
- Tests and user-facing documentation.

Excluded:

- Firmware, BLE protocol, GPIO, sampling, and packet-schema changes.
- Writing Agent replies back to the ESP32-C3 or OLED.
- Launching or reconfiguring the external Agent Gateway process.
- Authentication, public-internet exposure, speech input, TTS, and physical emergency-stop behavior.

## Design decisions

- Use Python standard-library `sqlite3`, `urllib.request`, and `http.server` so the GUI gains no new runtime dependency.
- Persist each instruction before its first HTTP attempt. Retries reuse the exact stored ID and text.
- Treat HTTP 202 as accepted, network errors/timeouts/503 as retryable, and 400/404/409/other HTTP responses as terminal failures.
- Persist valid callback events before returning HTTP 204. A duplicate `reply_id` also returns 204 without duplicate UI delivery.
- Run outbound requests and the callback server outside the Qt main thread. Reuse the GUI callback queue to update widgets safely.
- Keep the callback receiver independent of board connection so delayed replies are not lost.
- Gate only ordinary instruction submission on `ConnectionState.RECEIVING` when enabled. The explicit `停` shortcut bypasses this convenience gate and is labelled as non-physical emergency stop.
- Bind the callback server to `127.0.0.1` by default. LAN binding is an explicit operator choice because the contract currently has no authentication.

## Work breakdown

1. Create the feature branch and this living plan.
2. Add data models, SQLite store, outbound client/worker, and callback receiver.
3. Add focused unit tests for persistence, retry classification, callback validation, and de-duplication.
4. Add the Webhook tab and connect its controls to the new service layer and current connection snapshot.
5. Document local and LAN setup, Gateway environment configuration, limitations, and safe testing.
6. Run the full PC test suite, inspect the final diff, update this plan, commit, and push.

## Validation

```powershell
.\tools\project.ps1 pc-test
git diff --check
git status --short --branch
```

No firmware build, flash, serial monitor, or body-connected acquisition is required for this PC-only feature.

## Risks and rollback

- A callback listener can fail because its port is already occupied or Windows Firewall blocks LAN access. Surface the bind error in the tab and keep the rest of the GUI usable.
- A Gateway may be remote and unable to reach a loopback callback URL. Show the exact configured callback URL and explain that remote deployment requires a reachable LAN address.
- At-least-once callback delivery can duplicate UI effects unless persistence and `reply_id` de-duplication happen before acknowledgement. Keep this ordering covered by tests.
- SQLite or HTTP work on the Qt thread would freeze ECG rendering. Keep all blocking operations in worker threads.
- Rollback is limited to this feature branch and does not require firmware changes.

## Progress

- [x] Created the feature branch and ExecPlan.
- [x] Implemented Webhook service modules.
- [x] Added the Webhook GUI tab.
- [x] Added automated tests.
- [x] Updated user documentation.
- [x] Completed PC validation.
- [x] Committed and pushed the feature branch.

## Discoveries

- The current PC protocol has only ECG, IMU, and device-status packets, so the board cannot originate arbitrary user text without a future protocol extension.
- The Gateway callback URL is deployment-wide and startup-only; the PC tab can host and display the callback URL but cannot reconfigure a running Gateway through the documented API.
- The existing sandbox blocks pytest temporary-directory cleanup on this Windows machine; the unchanged test command passes when run outside the managed sandbox.
- An offscreen PySide6 smoke check constructed and closed all five GUI tabs successfully.

## Result

The PC-side Webhook service, GUI, tests, and operating guide are implemented, validated, committed, and pushed on the feature branch. No firmware or hardware operation was required.
