# Android ECG stream and waveform repair

## Goal

Make the Android raw trace preserve the received ECG and let analysis accumulate a usable window when firmware reports sampling warnings. Validate using packetized replay, not only a single perfect batch.

## Current state

The user supplied PC and phone screenshots: PC displays a full trace at about 458 Hz, while Android displays five vertices and never exits its ECG warmup. `collar_engine.py` clears its analysis deque for every flags bit in `0xFC`, then incorrectly draws from that deque instead of the independent raw ring. A 20-sample warning packet therefore replaces the window and `[::4]` reduces it to five displayed values. Flags are ORed across each firmware packet. Repeated SAMPLE_MISSED or clipping can trigger this without BLE packet loss.

## Scope

Android Python adapter, waveform model/rendering, streaming regression tests, APK build and authorized phone install/debugging. Preserve wire protocol, raw values, shared PC detector and firmware. No new human acquisition is required for reproduction.

## Design decisions

- Retain all received live samples and their flags in the raw ring. Draw actual values with device-time coordinates and visible breaks; remove stride decimation that can miss R peaks.
- Separate physiological validity from signal display. Sampling warnings must not discard the ECG window or prevent cleaning; retain strict HRV continuity gates. Use the PC clipping rule and make missing-data/lead-off states explicit.
- Reboot, historical replay, prolonged interruption and stale data still invalidate analysis/HRV. Never interpolate missing raw samples or silently join RR across a gap.
- Add received-window sample counts, effective sample rate and timing warnings to diagnostics.
- Exercise decoder → 20-sample packet adapter → native analysis on Android with synthetic data and warning flags. Initial runtime tests used only an ideal 5000-sample batch and missed this defect.

## Work breakdown

1. Reproduce five-point output from repeated warning packets.
2. Repair raw/analysis ownership and warning/gap handling.
3. Render full raw/cleaned traces with time/ADC axes and diagnostics.
4. Add packetized regressions and run Python, JVM, lint/build and device tests.
5. Install the corrected APK and record evidence and remaining live acceptance.

## Validation

Pinned Python adapter tests, Kotlin unit tests, APK/lint and Android packetized instrumentation. Compare the same synthetic samples to unchanged PC analysis under identical Python pins. Include repeated SAMPLE_MISSED, clipping, lead-off, actual gaps, reboot and staleness. Phone installation/debugging remains authorized by the conversation.

## Risks and rollback

Timing-warning windows can show cleaned signal/qualified HR but must not establish HRV or physiological Insight events. The complete raw trace costs more JSON/rendering work; measure Android replay duration. Revert this isolated Android fix if validation fails; leave PC and firmware untouched.

## Progress

- [x] Reviewed screenshots and traced warning-driven buffer resets.
- [x] Reproduced: 5000 received samples became a 20-sample analysis window and five plot vertices in the old adapter.
- [x] Repaired buffer ownership, flag handling, full traces and diagnostics; 16 Python regressions pass.
- [x] Built 0.1.1 (version code 2); 7 JVM tests and Android lint pass.
- [x] Installed on the local Android 16 x86_64 emulator; all 3 instrumentation tests pass (58.676 seconds), including packetized native analysis and the actual detail UI.
- [x] Install 0.1.1 on the Xiaomi and verify version code 2; subsequently upgraded to 0.1.2 for demo/capture work.
- [ ] User verifies the live BLE trace.
- [x] Recorded remaining hardware acceptance and reviewed the intended source/test/doc diff.

## Discoveries

- ADB initially reports no attached phone; the user has been asked to reconnect while implementation continues.
- The old test retained raw samples internally but never asserted that the exported raw trace used that ring. New tests compare all exported raw values, including a single-sample spike previously lost by stride decimation.
- Frequent SAMPLE_MISSED and modest timestamp drift need not indicate a broken Bluetooth stream. Warning-only windows now reach the shared PC cleaner and qualified HR; HRV and Insight remain gated. Actual missing packets remain explicit gaps and suppress metrics.

## Result

The old five-vertex failure is reproduced and fixed. The raw trace now exports all received samples, and repeated sampling warnings no longer keep analysis stuck at one packet. The same shared PC cleaner produces a complete 5000-point cleaned trace and fixture-matching HR/SQI on Android despite repeated SAMPLE_MISSED flags. HRV and Insight stay suppressed for timing-warning windows; lead-off and actual missing data additionally suppress HR. No raw interpolation or detector replacement was introduced.

Validation: 16 Python tests, 7 JVM tests, Android build/lint and 3 Android 16 emulator instrumentation tests passed. The replay uses 250 valid V0 packets fragmented into 20-byte chunks, with 44 ms device time per 20-sample packet, matching the warning scenario that starved the old app. Native output is 72.8155 BPM and SQI 0.6236, with all 5000 raw values preserved, complete cleaning and no HRV. Logs/screenshots are ignored under `.local-tools/`.

The replay was rerun after switching its screenshot capture to the rendered Compose root (1 test passed in 20.219 seconds). Visual review of `.local-tools/android-waveform-replay.png` confirms a full raw trace with a fixed 0–4095 ADC scale, device-time axis and a separately scaled cleaned trace. These are synthetic test data, not a live body recording.

Follow-up: 0.1.1 was successfully installed on the reconnected Xiaomi and its package version was verified. It was later upgraded to 0.1.2, documented in `2026-09-29-demo-and-diagnostics.md`. Live BLE/body-data validation remains unverified. The user now owns manual phone acceptance and requested that automated testing stop.
