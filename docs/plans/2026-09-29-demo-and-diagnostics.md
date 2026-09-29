# Companion demos and cross-host diagnostics

## Goal
Provide two independent Settings demos (AI Insight cards and ECG/heart rate), plus one-click PC/phone diagnostic captures and reproducible offline comparison.

## Current state
Android 0.1.1 is installed on Xiaomi 13 Ultra / Android 16. Sampling-warning window reset was fixed and synthetic replay passed; live BLE waveform recovery remains unconfirmed. PC already has experiment recording but no equivalent phone diagnostic export.

## Scope
Add explicitly synthetic presentation data, bounded local diagnostic capture, transport replay/comparison, documentation, tests, and install the new APK. Real provider API integration is deferred. No firmware/protocol changes or body-connected acquisition.

## Design decisions
- Demo flags are independent and persistent. Presentation data never enters the real repository, raw logs, history, or AI API. Existing acquisition controls show real service state. ECG demo suppresses automatic connection on the next launch; enabling it does not silently stop an existing hardware session.
- Capture recent transport bytes, decoded frames/analysis and connection boundaries into versioned JSONL. Memory retention is bounded to 60 seconds and 16 MiB; disk export happens on click. Capture metadata labels actual vs synthetic sources and dependency versions.
- PC and phone cannot be assumed to observe the same live stream. Offline replay uses one capture as the input to the PC algorithm and Android adapter. Separate captures with no overlapping device sample identities must not be reported as equivalent.
- Diagnostics stay local. Phone export uses an explicit share action or ADB; no automatic upload.

## Work breakdown
1. Implement independent demos and labels.
2. Implement PC/Android capture and offline replay/comparison.
3. Add targeted tests, run required checks, install and verify on available test device.
4. Document operations and verification limits; review, commit and push.

## Validation
Run `tools/project.ps1 pc-test`; pinned Python Android adapter tests; Gradle unit tests, assemble, lint and instrumentation. Exercise independent demo toggles and transport capture with synthetic data. Install via `adb install -r` and verify package version. No live physiological measurements are implied.

User override during implementation: stop automated tests and let the user test the phone manually. Do not resume automatic tests or UI navigation for this task without a new request. Existing completed checks below remain valid; incomplete phone UI checks are explicitly unverified.

## Risks and rollback
Diagnostic instrumentation must not block sampling on file I/O. Bounded buffers may omit older records and partial first frames; exports record eviction counts. Disable demos to return to live presentation; revert the feature commit for full rollback without device changes.

## Progress
- [x] Inspect code and previous fix.
- [x] Implement demos.
- [x] Implement capture/replay.
- [x] Build/lint, 10 JVM tests, 17 pinned Python tests and 226 PC tests pass; install 0.1.2 on Xiaomi and verify version code 3.
- [x] Stop phone instrumentation at user request and remove the standalone test APK; preserve the app and data.
- [x] Document operations and review intended diff.
- [ ] User performs manual UI and live BLE/capture acceptance.

## Discoveries
PC NeuroKit2 is 0.2.13; Android pins 0.2.10. Reports must include versions because SQI can differ even for identical samples.

The native AnalysisRuntimeTest completed on the Xiaomi. Instrumented UI/replay did not complete; the foreground alternated between launcher and MainActivity rather than the isolated test activity. No successful native demo/capture UI result is claimed. The final instrumentation termination message follows our deliberate force-stop after the user asked to stop, and does not establish a product crash.

## Result
0.1.2 is installed on the Xiaomi with existing application data preserved. Both independent demo switches, four labelled preset cards, rolling synthetic ECG, bounded PC/phone capture, explicit export/share, ADB collection helper and offline replay/comparison are implemented. Host replay verifies identical complete 5000-point raw/cleaned windows under the pinned environment. All completed automated checks predate the user's stop instruction; no further automated tests run after it.

Manual phone UI operation, phone diagnostic export/collection on actual hardware, and recovery of the user's live BLE waveform remain for user acceptance. PC GUI needs reopening to load the new capture button. Provider API integration remains deferred.
