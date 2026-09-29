# Smart Collar Companion

Android MVP for Xiaomi 13 Ultra / Android 16. Material 3 + Compose structure is
adapted from Heartwood; see [NOTICE](NOTICE.md) and [LICENSE](LICENSE). No Health
Connect dependency, data import, permission or background sync is enabled.

## Behavior

- **Today**: HR, HRV (RMSSD, ms), ECG, IMU and data quality/status. Tap metrics or
  ECG for Detail. No pairing or acquisition buttons here.
- **Settings**: scan and select a collar, start/stop, independent demos and local
  diagnostic export. The selected device persists locally. After setup, a new launcher
  task/process automatically starts collection. Rotation, tab switches and return
  from background preserve a manual Stop. Explicit Start resumes immediately.
- **Background**: one `connectedDevice` foreground service owns encrypted BLE,
  decoding and analysis. Private notification updates at most once per 2 seconds
  with HR/RMSSD and Stop. Unknown, stale or poor-quality metrics show `--`.
  A short-window RMSSD reference is explicitly labelled `≈` / `参考`.
- **AI Insight**: three concise cards for body rhythm, activity/rest and an
  everyday action, with locally mapped evidence chips and grouped history.
  Local fallback is labelled **本地解读**. Direct DeepSeek/OpenAI-compatible
  calls are off until configured in Settings. Last 30 explanations persist in private
  preferences; raw data stays in bounded memory until an explicit local diagnostic
  capture. Demo cards are separate from this history.

The BLE service uses the existing Nordic-UART-style UUIDs, safe 20-byte GATT
writes and notification stream assembly. A matching device ACK is required for
START/STOP. Update the collar firmware from this branch before using the app;
older firmware will produce a clear control-capability error. See
[control protocol](../docs/protocol/acquisition-control-v1.md).

## Phone-side algorithms

Chaquopy packages **unchanged** PC `analysis.py`, `buffers.py`, `protocol.py`,
`source_coordinator.py` and `health_motion.py` through a Gradle Sync task. The
Android adapter is `app/src/main/python/collar_engine.py`. It uses the latest
5000 received ECG samples (10 seconds at nominal 500 Hz), NeuroKit2 cleaning/peaks/quality and the existing
recent-RR median HR. Native scientific packages are pinned in
`app/requirements-android.txt` (Python 3.10, NumPy 1.23.3, SciPy 1.8.1,
NeuroKit2 0.2.10). Desktop PC dependencies themselves remain unchanged.

Raw counts and original flags are retained in a separate bounded ring. Both
traces include every sample, device-time coordinates and explicit breaks at
missing data; the UI does not stride-decimate R peaks. Starting with 0.1.3 the
phone displays the latest 3 seconds by default, with shared 3/5/10-second controls
for raw and cleaned views. Only the viewport is cropped: 5000 analysis points and
diagnostic output remain intact. Time ticks are relative to the newest sample.
Vertical ADC tick labels and automatic/fixed ranges distinguish signal amplitude
from screen geometry; fixed ranges are 0–4095 raw and -2048–2048 cleaned counts.
These are counts, not a calibrated mV/paper-speed display. Diagnostics show window
sample counts, effective sample rate and timing warnings. Analysis runs away
from the main thread, at most once per second, after draining queued packets.
Sampling-warning and occasional clipping packets remain in the analysis window.
The unchanged PC cleaner runs on that window. Starting with 0.1.5, a private
analysis copy maps the ADC clipping bit to actual rail hits (0 or 4095) before
applying the PC's 80% sustained-clipping rule. Packet flags are OR-aggregated and
cannot describe the number of individually clipped samples. Original values and
wire flags remain intact in both rings and diagnostic capture.
Lead-off, actual missing packets and queue/transport overflow suppress
HR/HRV while retaining the traces. Timing warnings permit quality-qualified HR
with a warning but suppress qualified HRV. Insight uses the fresh displayed
metrics, including explicitly labelled reference HRV, for everyday observations.
Hardware explanations remain in Detail/Settings. Reboot, a gap over one
second, historical data, reconnects and staleness reset analysis continuity. MIC1
frames are consumed whole, so audio payload cannot masquerade as ECG.

Qualified RMSSD requires a window free of sampling/quality problems and uses unique settled R peaks from overlapping windows, a rolling 60-second
quiet interval, at least 30 RR intervals, SQI ≥0.5 and motion score <10. RR outside
300–2000 ms or adjacent changes >25% reset the HRV window. These are conservative
engineering gates, not a validated stress model or medical threshold. Motion
retains the PC's 30-second coverage rules. No HRV-to-stress mapping is implemented.

HRV status is now explicitly `waiting`, `blocked`, `collecting` or `ready`, with
`hrv_reasons` explaining each current blocker. Today, Detail and the notification
surface those reasons. `window_flags` and `flag_counts` are retained in diagnostic
analysis records. Counts mean sample slots covered by packet flags, not exact
missed ADC conversions. The quality gates are unchanged: a displayed heart rate
does not establish valid RMSSD, and recurring sampling/clipping flags can keep
the qualified HRV quiet-window progress at zero indefinitely.

Starting with 0.1.5, **Today, Detail and the notification display a separate
short-window RMSSD reference** when qualified HRV is unavailable. It is computed
from at least three consecutive valid settled RR intervals in the current ECG
analysis window, after the existing HR/SQI checks pass. RR uses available device
timestamps; within-packet timestamps still use nominal interpolation. Intervals
outside 300–2000 ms, over 25% from nominal timing, or adjacent changes over 25%
break the reference segment. No estimate bridges rejected intervals. Timing
warnings, sporadic clipping, unavailable IMU or motion can leave a reference
visible with explicit uncertainty; they still prevent qualified resting HRV.
Starting with 0.1.6, the reference is sent to AI as a reference metric and can be
interpreted in everyday cards. It is not relabelled as qualified RMSSD or a stress score.
True losses/lead-off, stale data, sustained saturation and poor SQI suppress it.
This displays a measured short-window calculation; it does not repair analog
saturation or establish physiological accuracy.

Diagnostics add `rmssd_reference`, `hrv_reference_rr_count`,
`hrv_reference_window_s`, `hrv_reference_reasons`, `adc_clipped_samples`,
`adc_flagged_sample_slots` and `analysis_clipping_rule`. Existing `rmssd` remains
the qualified metric. Detail distinguishes actual rail hits from flagged slots.

## Build and test

Install JDK 17, Android SDK platform 36/build tools and Python 3.10. From this
directory (PowerShell):

```powershell
$env:JAVA_HOME = 'path/to/jdk-17'
$env:ANDROID_HOME = 'path/to/android-sdk'
$env:HTTP_PROXY = 'http://127.0.0.1:7897'
$env:HTTPS_PROXY = $env:HTTP_PROXY
.\gradlew.bat :app:testDebugUnitTest :app:assembleDebug :app:lintDebug `
  '-PbuildPython=path/to/python3.10.exe' `
  '-Dhttp.proxyHost=127.0.0.1' '-Dhttp.proxyPort=7897' `
  '-Dhttps.proxyHost=127.0.0.1' '-Dhttps.proxyPort=7897'
```

Omit proxy arguments if not required. SDK/JDK paths, downloaded toolchains and
local logs must remain ignored. APK: `app/build/outputs/apk/debug/app-debug.apk`.
Runtime smoke tests use `:app:connectedDebugAndroidTest` with an explicitly
selected test device (`ANDROID_SERIAL`) and the same Python build property.
Instrumentation replays synthetic ECG only and checks page responsibilities.
`StreamingReplayTest` feeds 250 V0 packets through 20-byte transport fragments,
with repeated SAMPLE_MISSED flags and device-time drift, then verifies all 5000
raw values, cleaned output, HR parity and the detail UI. It does not connect BLE.

Python adapter tests: install the scientific dependencies from the Android pins
where desktop wheels exist (PyWavelets 1.4.1 substitutes the old Android wheel;
the shared ECG path does not use wavelets), then from the repository root:

```powershell
python -m pytest android_app/tests
.\tools\project.ps1 pc-test
```

Golden decoder tests read the shared JSON vectors from `docs/protocol`, including
every byte split, noise/CRC recovery, MIC1 isolation and acquisition commands.

## Demonstrations and local diagnostics

Settings has independent persistent AI Insight and ECG/heart-rate demo switches.
All demo data is synthetic and labelled; it never replaces acquisition buffers
or enters the API/event pipeline. ECG demo prevents auto-connect on a fresh launch,
while already-running real acquisition remains explicitly controllable in Settings.

PC and phone can save the last 60 seconds of bounded transport/analysis evidence.
See [demo and capture instructions](../docs/android-demo-diagnostics.md) for export,
ADB collection, same-input offline replay and cross-host comparison semantics.

## Direct AI API (introduced in 0.1.4; everyday cards in 0.1.6)

Settings supports **DeepSeek 官方** and **OpenAI 兼容 API**. Enter your personal
API key on the phone, choose the provider/model, enable AI and save. Then use
**读懂此刻** on AI Insight. **用示例检查 API** in Settings makes one explicit
paid request using synthetic data (including reference HRV) and shows three temporary cards without adding
to real history. Both demo switches must be off for any network request.

Automatic generation is a separate opt-in and accepts reference metrics and
ordinary snapshots. Local everyday cards continue without cloud access.
Provider failures display an actionable error in Settings and pause automatic AI
until successful manual retry or saving configuration. There are no automatic
HTTP retries. Previous hidden gateway settings are no longer used or migrated.

See [configuration, privacy, JSON contract and manual acceptance](../docs/android-ai-api.md).

## Acceptance limits

See the [living plan](../docs/plans/2026-09-28-android-companion-mvp.md) for actual
MVP validation results and the [0.1.1 waveform repair record](../docs/plans/2026-09-29-android-ecg-stream-fix.md)
for the streaming regression and current phone-install status. APK compilation and synthetic replay do not prove long-run
BLE stability, Xiaomi background survival, battery use or physiological accuracy.
The bundled older scientific wheels require a separate compatibility gate on
16 KB page-size devices. The target Xiaomi reports 4 KB pages.

The Android screen may run over USB for synthetic/software tests. For live human
ECG, the repository's battery-only wireless acquisition rules still apply.
