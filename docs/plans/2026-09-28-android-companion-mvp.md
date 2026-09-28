# Smart Collar Companion Android MVP

## Goal

Run the collar → Android live metrics → explanatory Insight loop on Xiaomi 13 Ultra (Android 16), using Material 3 / Compose. Reuse the PC NeuroKit2 analysis rather than silently substitute a new detector. No Health Connect in this milestone.

## Current state

At the start of this change, firmware V0 emitted 500 Hz raw ECG, 50 Hz IMU and device status over encrypted BLE UART, sampled from boot and had no acquisition command. The PC owned NeuroKit2 cleaning, R peaks, RR, HR and motion analysis. Heartwood provides the GPL-3.0-or-later Gradle/Compose starting point; Google Health Samples informs architecture. The new app lives in `android_app/`; optional V0 control types 7/8 and their shared vectors live alongside the existing firmware and PC protocol.

## Scope

Android app, stream decoding, phone analysis bridge, foreground service, bounded reconnect, device selection, Today/Detail/Settings/Insight, local event explanations and an optional HTTPS Insight gateway client. Add a documented firmware collection control extension. The user explicitly authorized app installation and debugging on the attached Xiaomi on 2026-09-29, then authorized firmware flashing and confirmed no electrodes were connected. No human acquisition, Health Connect or medical diagnosis.

## Design decisions

- Today only displays metrics, ECG, IMU and state. Connection and collection controls live in Settings; notification includes Stop.
- Once a device is selected, a fresh launcher session automatically starts. Stop affects that session; navigation, rotation and background return do not restart. A new launcher session may restart. No boot receiver or automatic OS restart after a user Stop.
- One foreground connected-device service owns BLE and analysis. Persist only chosen device and bounded derived event history; keep live raw ring buffers separate from cleaned output. Invalid/gapped/historical data never silently produce a fresh HRV.
- HRV is RMSSD in milliseconds; no stress score. Body events include quality, coverage, motion, source and timestamp.
- Kotlin handles transport and framing. Chaquopy packages the existing Python analysis modules. Golden vectors and desktop replay tests gate protocol/algorithm parity; physical device performance remains an acceptance gate.
- START/STOP require device confirmation. Older firmware must be identified explicitly; disconnecting the phone is not represented as stopping hardware.

## Work breakdown

1. Establish buildable Compose app and strict V0 decoder with shared golden vectors.
2. Integrate shared NeuroKit2 and IMU processing on a worker thread with bounded buffers.
3. Implement BLE discovery/bonding/subscription, foreground service, reconnect and session lifecycle.
4. Implement firmware control with acknowledgement and protocol compatibility tests.
5. Wire live screens and structured Insight pipeline; document server contract and offline behavior.
6. Build, run tests, inspect diff and record remaining hardware gates.

## Validation

Android: `./gradlew :app:testDebugUnitTest :app:assembleDebug :app:lintDebug`.
Python bridge: `python -m pytest android_app/tests` with `PYTHONPATH=pc_app/src;android_app/app/src/main/python`.
Firmware changes: `./tools/project.ps1 build`, `./tools/project.ps1 size`; protocol changes also `./tools/project.ps1 pc-test` and shared golden vectors.
Hardware: Xiaomi BLE permissions/bonding, 30-minute battery wireless capture, rotation/background/notification Stop/relaunch, radio interruptions, RMSSD warmup and bad-data suppression, 16 KB page-size native-library loading where applicable.

## Risks and rollback

Native scientific wheels may not support the target Android runtime or page size. A successful APK build alone does not settle this. Keep Python pins explicit and surface analysis errors. BLE firmware mismatch is a capability error, never a synthetic success. New work is isolated on `feat/android-companion-mvp`; existing PC behavior remains available.

## Progress

- [x] Repository and protocol reviewed; branch and Android Gradle scaffold created.
- [x] Decoder, shared protocol vectors and analysis adapter tests pass.
- [x] BLE/service/UI and structured Insight client implemented; APK builds and lint passes.
- [x] Firmware control compiles; Python tests check the C golden literals against shared JSON.
- [x] APK installed on Xiaomi 13 Ultra; native analysis synthetic replay passes on Android 16.
- [x] Unified firmware flashed to the confirmed ESP32-C3 v1.1 / 4 MB bench device; all four written images passed hash verification.
- [x] Build/test results and remaining acceptance gates recorded.
- [ ] Physical collar START/STOP ACK and continuous BLE stream verified.
- [ ] Thirty-minute wireless capture, notification Stop and Xiaomi background behavior verified.
- [ ] Deployed model gateway and real AI replies verified.

## Discoveries

- Status payload contains one reserved byte and a uint16 flags field before the counters.
- The build host initially has no JDK or Android SDK on PATH.
- Python 3.10 is needed for the available Android SciPy 1.8.1 wheel; pins must be tested as a complete dependency set.
- SciPy's Android wheel requires NumPy 1.23.3. A newer NumPy pin is incompatible; the complete pinned set builds successfully with Chaquopy 17.0.0.
- The target phone reports Android 16 and 4096-byte pages. Its native runtime loads NumPy/SciPy/NeuroKit2 and matches the desktop synthetic ECG fixture. This does not establish 16 KB page compatibility.
- MIC1 and V0 share the notification stream. The decoder consumes complete CRC-valid MIC1 packets before looking for ECG magic, including when an audio payload contains a valid-looking V0 packet.
- Native imports are preloaded before accepting samples. Queued frames retain their reception time; analysis rechecks freshness after the calculation and suppresses expired metrics.
- GATT callbacks run on the main handler with service connection state. Only validated device ACKs establish acquisition state; a completed GATT write alone is insufficient.
- The repository-wide `data/` ignore pattern also matched Kotlin source packages. Narrow Android source exceptions ensure transport, repository and lifecycle files are included in the commit without including captured data.

### Validation results, 2026-09-29

- `:app:testDebugUnitTest :app:assembleDebug :app:lintDebug :app:assembleDebugAndroidTest`: success; 7 JVM tests, no lint errors. Deprecation/version warnings remain.
- `python -m pytest android_app/tests -q` in the pinned desktop analysis environment: 12 passed. Matplotlib emits upstream Pyparsing deprecation warnings.
- `./tools/project.ps1 pc-test`: 220 passed before adding one additional C-literal consistency test. The final focused `pc_app/tests/test_acquisition_control.py` run has 7 passing tests, including that addition.
- `./tools/project.ps1 build`, followed by a second incremental build and `./tools/project.ps1 size`: success under ESP-IDF 6.0.2. Image size 675597 bytes; DRAM 132763 bytes (41.32%). The C startup self-test is compiled; its runtime result has not been observed in serial logs.
- Android instrumentation on the physical Xiaomi: native-library/synthetic parity and Today/Settings navigation both pass on the final installed build (`OK (2 tests)`, 7.207 seconds). A prior run was interrupted by lock screen and was stopped before this clean rerun.
- Final Today screenshot was visually inspected on the phone. The configured device connection reached the control timeout path and displayed “设备未确认采集控制；请更新支持控制协议的固件”. No START ACK or live ECG was established; this is an observed compatibility failure, not successful end-to-end acquisition.
- Synthetic fixture: 5000 generated ECG samples, desktop HR 72.81553398058253 BPM and SQI 0.6236363609044285. No human ECG fixture or real-time collar measurements are claimed.
- Local build/test logs are ignored under `.local-tools/`; reproducible commands and dependency pins are in `android_app/README.md`.
- After explicit flash authorization and confirmation that electrodes were disconnected, `esptool --chip esp32c3 ... flash-id` confirmed ESP32-C3 v1.1 and 4 MB Flash. `./tools/project.ps1 flash` succeeded: bootloader, partition table, speech models and application all passed written-data hash verification, followed by the tool's hard-reset action. Flash log: ignored `.local-tools/firmware-flash.log`. No serial monitor was opened. ADB then reported no attached phone, so post-flash Android START/STOP ACK and live stream validation remain pending.

## Result

The first runnable Android increment is installed on the test phone. Today displays metrics and ECG; Settings owns pairing and acquisition; notification controls and task-scoped automatic acquisition are implemented. The app packages the existing PC analysis modules without forking their algorithms and adds conservative mobile quality/freshness/RMSSD gates. Local Insight cards and the optional structured-event gateway client are implemented; no model provider or server has been provisioned.

The complete live collar → phone → cloud-AI loop remains a hardware/service acceptance gate. The collar now has this branch's firmware control extension, with flash writes verified. Post-flash BLE control, live signal accuracy, long-run background survival and cloud responses have not been verified. Existing PC collection remains available; Android changes are isolated on `feat/android-companion-mvp`.
