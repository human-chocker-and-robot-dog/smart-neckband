# Unified ESP32-C3 Sensor and Hi ESP Firmware

## Goal

Ship one default ESP32-C3 image that continuously samples ECG and IMU, drives
the OLED, runs the official Hi ESP WakeNet model, and sends MIC1 ADPCM audio on
the same encrypted BLE connection consumed by the main PC GUI.

## Current state

- Production firmware sends V0 ECG, IMU, and status packets over encrypted BLE.
- The standalone INMP441 experiment owns a separate plaintext BLE connection
  and implements Hi ESP, MIC1, ADPCM, and PC-side ASR/VAD.
- The historical `-Voice` build uses device Wi-Fi and device-side Volc ASR and
  is not the target architecture.
- ESP32-C3 is single-core, has no assumed PSRAM, and uses a 4 MB flash.

## Scope

Included:

- Extract a reusable microphone runtime from the standalone experiment.
- Add model storage and `wn9s_hiesp` to the default production image.
- Serialize V0 and MIC1 frames through one encrypted BLE UART characteristic.
- Give V0 sensor traffic strict priority over MIC1 audio traffic.
- Add a mixed-stream PC demultiplexer and make the main GUI the only production
  BLE owner for sensor and microphone traffic.
- Auto-arm Hi ESP after subscription and re-arm after PC VAD sends `MIC STOP`.
- Keep the standalone microphone firmware and a sensors-only build as
  diagnostics.

Excluded:

- Flashing hardware, serial monitor use, body-connected acquisition, OTA, or
  claims of hardware timing validation without supplied logs.
- PCM16/PCM8 transmission in the unified image.
- Device-side Wi-Fi or Volc ASR.

## Design decisions

- Task priorities: ECG 11, I2S capture 9, IMU 8, packet assembly 7, BLE TX 6,
  WakeNet/ADPCM 5, OLED 3.
- I2S capture writes into four fixed PCM blocks and never waits for WakeNet,
  BLE, or logging.
- MIC1 uses 400-sample, 25 ms IMA-ADPCM frames. Audio drops before sensors.
- V0 and MIC1 wire formats remain unchanged. A PC demultiplexer owns the mixed
  byte stream and validates each inner CRC before dispatch.
- Default production partition layout keeps a 2 MiB app, adds a 256 KiB model
  partition at `0x210000`, and moves storage to `0x250000` with 1728 KiB.
- Default build is unified. `-SensorsOnly` is diagnostic. Historical `-Voice`
  is removed from active build and operator paths.
- Unified controls are `MIC ARM`, `MIC DISARM`, `MIC STOP`, and
  `MIC SHIFT <10..20>`.

## Work breakdown

1. Create reusable MIC1 encoding, WakeNet, I2S, state, and command modules.
2. Extend the production BLE transport item size and add bounded low-priority
   audio enqueue with connection-parameter reporting.
3. Start the microphone runtime after sensors and packet tasks; failures remain
   non-fatal to sensor acquisition.
4. Update the default model partition, sdkconfig, dependencies, build wrapper,
   and firmware version.
5. Add a PC mixed-stream demultiplexer and route MIC1 frames through the
   existing `BlePacketReader` before integrating the main GUI controls.
6. Refactor microphone ASR/VAD UI logic into a reusable panel/controller while
   retaining `pc-mic` for the standalone `CollarMic-*` diagnostic image.
7. Add C/Python goldens, parser/priority/state tests, and operator docs.
8. Run PC tests, syntax checks, unified and diagnostic firmware builds, size
   gates, and offscreen GUI smoke tests.

## Validation

- `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\project.ps1 pc-test`
- PowerShell parser for repository scripts and Python `compileall`.
- Default unified ESP32-C3 build and size.
- ESP32-C3 `-SensorsOnly` build and size.
- Standalone INMP441 build and size.
- App partition at least 20% free and static DRAM at most 65%.
- Model build report contains `wn9s_hiesp` and `srmodels.bin` is not a
  four-byte placeholder.
- `git diff --check` and focused review of generated artifacts and docs.

## Risks and rollback

- WakeNet CPU time may delay the 2 ms ECG task. ECG remains higher priority and
  hardware acceptance requires zero missed notifications and ring overflow.
- BLE throughput may drop MIC1 frames. The audio queue is bounded and never
  displaces V0 packets.
- A new partition layout requires a full flash. No flash occurs in this task.
- The sensors-only diagnostic build and standalone microphone image remain
  available until hardware gates pass.

## Progress

- [x] Confirm product choices: unified PC GUI, automatic wake standby, and
  replacement of the historical Voice build.
- [x] Implement unified firmware runtime and transport.
- [x] Implement PC mixed-stream ownership and UI.
- [x] Add tests and documentation.
- [x] Complete build and software validation.
- [x] Commit locally without pushing.

## Discoveries

- The existing production and microphone pin maps are already compile-time
  checked for overlap.
- V0 traffic is roughly 3 KiB/s and MIC1 ADPCM roughly 9.4 KiB/s, which fits
  the negotiated MTU 256 design when connection interval is kept at 7.5-15 ms.
- The historical Voice build proves ESP-SR plus sensor firmware can fit in the
  C3 image, but its model and ASR architecture are intentionally retired.

## Result

The default ESP32-C3 image now combines ECG, IMU, OLED, Hi ESP WakeNet, and
MIC1 ADPCM on one encrypted BLE connection. The sensors-only rollback and
standalone INMP441 diagnostic builds remain available. The unified image uses
673,808 bytes of the 2 MiB app partition with 68% free and 41.30% static DRAM;
the sensors-only image uses 543,904 bytes with 74% free and 37.74% static DRAM.
The official `wn9s_hiesp` model image is 125,943 bytes. All 195 PC tests,
Python compile checks, PowerShell parser checks, offscreen main-GUI
construction, and `git diff --check` passed. Hardware bench gates remain
explicitly unverified because this task did not flash, monitor, or connect
body electrodes.
