# AI Smart Collar Handoff

Last updated: 2026-07-19.

## Current Branch State

- Current checked-out branch: `feat/v0-foundation`.
- Current checked-out commit: `9327381 fix(live): resume ingest sequence after restart`.
- Main baseline branch: `main` at `8c56a15 chore: establish ESP-IDF hello world baseline`.
- Separate Live Beta test branch: `fix/live-fb892fa-reliability` at `39dc916 fix(live): adapt mobile ECG and settings`.
- No Git remote is configured in this local repository at the time of this handoff, so push is skipped unless a remote is added later.

Important branch note:

`fix/live-fb892fa-reliability` is an experimental Live Beta webpage branch. It is not the primary V0 firmware, Bluetooth SPP, PC parser, ECG, or IMU task branch. Treat it as a test branch for the public/web viewer experience. Do not use it as evidence that the core V0 hardware or medical-adjacent acquisition path has been validated.

## Repository Rules To Keep

- Read `AGENTS.md` before changing code or running commands.
- On Windows or PowerShell work, use the checked-in `powershell-command-runner` Skill under `.agents/skills/powershell-command-runner/`.
- Do not flash, erase flash, open a blocking monitor, change eFuses, or perform body-connected acquisition without explicit user instruction.
- Do not connect desktop USB, wall power, a charging power bank, or grounded bench instruments while electrodes are attached to a person.
- Do not claim body-connected behavior is validated unless battery-powered wireless test logs are supplied.
- Keep raw ECG as raw ADC counts in firmware. Filtering, R peaks, HR, RR, HRV, and SQI belong in the PC application.
- Commit coherent validated changes with Conventional Commits. Push only if an upstream exists.

## Hardware Baseline

- Board: classic ESP32 / ESP-WROOM-32, not ESP32-S3.
- ESP-IDF: v6.0.2.
- Flash: 4 MB.
- Target: `esp32`.
- Bench USB port: `COM18`.
- Windows Bluetooth SPP outgoing COM observed: `COM19`.
- Windows Bluetooth local placeholder COM observed: `COM20`.
- ECG input: GPIO34 / ADC1_CH6.
- AD8232 LO-: GPIO25.
- AD8232 LO+: GPIO26.
- I2C SDA: GPIO21.
- I2C SCL: GPIO22.
- OLED: `0x3C`.
- MPU6050-compatible IMU: `0x68`, observed `WHO_AM_I=0x72`.
- ECG sample rate: 500 Hz.
- IMU sample rate: 50 Hz.

Recent hardware discovery:

The AD8232 output was accidentally plugged into the wrong interface, VN. After correcting the wiring away from VN, the PC app showed a real ECG waveform. This strongly indicates the previous all-zero ADC trace was wiring, not Bluetooth or parser corruption.

## Core V0 Firmware And PC App Status

The main V0 path implemented so far is:

```text
ESP32 sensor sampling
-> Bluetooth Classic SPP
-> Windows virtual COM
-> Python binary parser
-> raw binary log
-> ECG/IMU buffers
-> PySide6 + PyQtGraph GUI
```

Implemented firmware pieces:

- Board configuration in `firmware/main/board_config.h`.
- 500 Hz ECG sampling on GPIO34 / ADC1_CH6 via GPTimer notification and `adc_oneshot_read()` in the sampling task.
- 50 Hz MPU6050 raw six-axis readout.
- Lead-off GPIO state from GPIO25/GPIO26.
- Independent ECG and IMU ring buffers.
- Binary V0 protocol packets for `ECG_BATCH`, `IMU_BATCH`, and `DEVICE_STATUS`.
- Bluetooth Classic SPP acceptor named `SmartCollar-V0`.
- SPP TX queue with write-complete/congestion handling.
- OLED status pages, refreshed outside sampling and SPP callbacks.

Implemented PC pieces:

- `pyserial` COM reader.
- Binary stream parser with magic resync, length checks, CRC checks, and sequence gap tracking.
- Raw binary recording.
- ECG and IMU ring buffers.
- PySide6 + PyQtGraph GUI.
- NeuroKit2 ECG analysis worker off the GUI thread.
- Raw ECG display, cleaned ECG display, R-peak markers, HR, RR, SQI, lead-off, packet loss, and CRC counters.
- IMU roll/pitch/yaw complementary filter and OpenGL cuboid display.
- `Calibrate Flat` control that zeroes current orientation and captures stationary gyro bias.

## Known Fixes Already Applied

- OLED `ERR` no longer counts expected disconnected Bluetooth drops.
- OLED page switching restored to readable speed, 3 second page interval.
- Classic BT discoverability fixed with later scan-mode setup, EIR, and no-input/no-output pairing behavior.
- SPP stale TX packets are dropped on connect/disconnect so reconnects do not masquerade as current data.
- PC COM list prioritizes outgoing Bluetooth SPP ports, for example `COM19 ... BT OUT`.
- PyOpenGL was added to the GUI extra for the 3D view.
- NeuroKit2 and NumPy warnings are suppressed or avoided for too-few-peaks and no-finite-SQI windows.
- ECG clipping windows report `ECG clipped` instead of attempting HR.
- MPU6050 all-zero 14-byte samples are rejected in firmware and trigger the existing 1 Hz IMU reinitialization path.
- HR display uses a median of recent physiologically plausible RR intervals rather than only the last two R peaks.
- IMU flat calibration subtracts current gyro zero-rate bias before integration.

## Current Validation Snapshot

Most recent documented validation on the V0 path:

- `.\tools\project.ps1 pc-test`: 18 tests passed.
- `.\tools\project.ps1 build`: passed.
- `.\tools\project.ps1 size`: passed.
- `git diff --check`: passed.
- `idf.py -C firmware -p COM18 -b 460800 flash`: previously passed with bootloader, partition table, and app hash verification.

Important caveat:

The latest PC-only HR smoothing and gyro-bias calibration did not require a firmware flash. The later firmware-side all-zero IMU rejection was built and size-checked, but only flash it when the user explicitly requests it and body electrodes are not connected to a USB-powered setup.

## How To Run The PC GUI

Use the outgoing Bluetooth SPP COM port, not the local placeholder port.

```powershell
cd C:\Users\XWen1024\Documents\smart-neckband\pc_app
py -3.12 -m smart_neckband
```

In the GUI:

- Select the port marked `BT OUT`, usually `COM19`.
- Use `Calibrate Flat` only when the IMU is physically still and flat.
- Expect yaw to drift over time because MPU6050 has no magnetometer. The software can reduce gyro bias, but it cannot provide absolute yaw.

## How To Build And Flash Firmware

Build and size:

```powershell
. 'C:\Espressif\tools\Microsoft.v6.0.2.PowerShell_profile.ps1'
.\tools\project.ps1 build
.\tools\project.ps1 size
```

Reliable direct flash shape observed on this machine:

```powershell
. 'C:\Espressif\tools\Microsoft.v6.0.2.PowerShell_profile.ps1'
idf.py -C firmware -p COM18 -b 460800 flash
```

Do not flash while body electrodes are attached to a person through a USB-powered setup.

## Live Beta Web Branch

There is a separate Live Beta web effort in this repository. It is useful, but it is not the core hardware task.

Branches:

- `feat/v0-foundation`: currently contains the core V0 work and some live-uploader reliability work.
- `fix/live-fb892fa-reliability`: experimental Live Beta webpage branch with mobile ECG/settings adaptations.

Live Beta files include:

- `api/`
- `lib/`
- `src/`
- `tests/live-heartbeat.test.ts`
- `docs/live-heartbeat-vercel.md`
- `docs/plans/2026-07-15-vercel-live-heartbeat.md`
- `package.json`
- `vercel.json`

Live Beta purpose:

```text
Windows uploader
-> Vercel WebSocket Function
-> Redis Pub/Sub/snapshot
-> public browser viewer
```

The Live Beta service uploads PC-derived clean ECG, R peaks, HR, SQI, and lead-off/status data. It must not replace the raw firmware protocol, the local PC raw binary log, or the safety rules for body-connected testing.

Live Beta validation commands:

```powershell
npm test
npm run lint
npm run typecheck
npm run build
git diff --check
```

Known Live Beta facts:

- Vercel WebSockets are Public Beta.
- Redis is required for durable session state, sequence, snapshot, and Pub/Sub.
- The fixed public page is `/live`.
- The PC uploader is `smart_neckband.live_uploader`.
- A production smoke test previously reached `https://heart.xwenlabs.com/live` with acknowledged batches and zero CRC/packet loss in that run.
- This is an electronics transport/web test, not medical validation.

## Open Issues And Next Steps

Core V0:

- Confirm whether the latest firmware all-zero IMU rejection has been flashed to the ESP32.
- Run a battery-powered wireless-only ECG session before making any body-connected claims.
- Continue evaluating HR stability using real clean ECG after the VN wiring correction.
- If HR still jumps, log detected R peaks and RR intervals per analysis window so false positives/false negatives are visible.
- If yaw still spins after `Calibrate Flat`, collect 10 seconds of stationary gyro data and estimate bias variance. Without a magnetometer, long-term yaw drift cannot be eliminated.
- Improve GUI status visibility for ADC clipping, sample missed, IMU offline, and analysis state.

Live Beta:

- Keep `fix/live-fb892fa-reliability` isolated unless the user explicitly asks to merge or port specific web changes.
- If continuing Live Beta, validate on the target branch with `npm test`, `npm run lint`, `npm run typecheck`, `npm run build`, and `git diff --check`.
- Do not commit `.env.local`, real Redis URLs, tokens, captured ECG data, or production secrets.

## Do Not Infer

- Do not infer medical correctness from Heart Monitor LED behavior.
- Do not infer safe body-connected behavior from USB bench tests.
- Do not infer Bluetooth health from Windows paired status alone. SPP is connected only when an RFCOMM COM client opens the outgoing port.
- Do not treat the Live Beta webpage branch as the source of truth for V0 firmware acceptance.
