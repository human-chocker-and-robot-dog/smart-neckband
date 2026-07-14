# V0 Foundation and Real-Time Loop Implementation

## Goal

Implement the V0 path from `docs/V0_Codex_ESP-IDF_Classic_ESP32_Software_Plan.md`: build from the foundation firmware into the shortest end-to-end loop of ESP32 sensor sampling, Bluetooth Classic SPP packet transport, Windows Python parsing, ECG visualization, and IMU attitude display.

## Current state

- Branch: `feat/v0-foundation`.
- Firmware builds for classic ESP32 / ESP-WROOM-32 with ESP-IDF v6.0.2.
- `sdkconfig.defaults` targets `esp32`, 4 MB flash, and custom `partitions.csv`.
- `partitions.csv` reserves a 2 MB factory app partition and remaining SPIFFS storage.
- GPIO baseline: ECG `GPIO34 / ADC1_CH6`, AD8232 `LO-=GPIO25`, `LO+=GPIO26`, I2C `SDA=GPIO21`, `SCL=GPIO22`.
- 2026-07-15 hardware rescan confirmed OLED at `0x3C` and MPU6050 at `0x68`.
- Protocol V0 currently has an ECG golden vector and Python pytest coverage.
- The project-local `powershell-command-runner` Skill is present with `agents/openai.yaml`, `core/scripts`, `core/pattern-catalog`, and `core/tests/run-smoke.ps1`.
- Existing uncommitted documentation changes in `AGENTS.md` and `docs/V0_Codex_ESP-IDF_Classic_ESP32_Software_Plan.md` are preserved.

## Scope

Included:

- Firmware board configuration header for pins, sample rates, firmware version, and flash configuration.
- Startup self-test logging for target, chip revision, core count, detected flash size, configured flash size, firmware version, heap, and partition summary.
- I2C master initialization and one-shot address scan from `0x08` through `0x77`.
- MPU6050 initialization and 50 Hz raw six-axis reads.
- GPIO34 / ADC1_CH6 500 Hz raw ECG sampling via GPTimer notification and `adc_oneshot_read()` from a sampling task.
- Lead-off GPIO reads for AD8232 LO-/LO+.
- Independent ring buffers and counters for sampling, packetization, and transport overflow.
- Transport-independent C/Python protocol support for `ECG_BATCH`, `IMU_BATCH`, and `DEVICE_STATUS` packets with golden vectors.
- Bluetooth Classic SPP acceptor named `SmartCollar-V0`, with a separate TX queue and congestion/write-complete handling.
- OLED status pages refreshed at no more than 2 Hz.
- PC-side serial reader, binary stream parser, raw binary recording, data publisher interface, and PySide6/PyQtGraph GUI scaffold for ECG and IMU.
- Validation through repository wrappers: doctor, firmware build, size, PC tests, and `git diff --check`.

Excluded:

- Flashing, monitor, erase, or body-connected acquisition unless separately requested.
- BLE, WebSocket, browser-based UI, and phone transport.
- Medical diagnosis claims or firmware-side ECG filtering.
- Claiming SPP or sensor behavior has been verified on hardware without a real run log.

## Design decisions

- Keep all board pins and target sample rates centralized in `firmware/main/board_config.h`.
- Use ESP-IDF I2C master driver for MPU6050/OLED/status work; do not put I2C work in any high-frequency ECG loop.
- Use GPTimer at 2 ms to notify a high-priority ECG task; the ISR only sends task notifications.
- Keep the primary ECG stream as raw ADC counts. No firmware digital filtering, BPM, R-peak detection, or smoothing is allowed on the transmitted ECG stream.
- Keep C packet structs packed and encode all wire bytes little-endian; Python golden vectors assert byte-for-byte compatibility.
- Use CRC-16/CCITT-FALSE for packet integrity in V0 protocol helpers.
- Use packet type `1` for `ECG_BATCH`, `2` for `IMU_BATCH`, and `3` for `DEVICE_STATUS`.
- Batch ECG as 20 samples per packet at 25 packets per second and IMU as 2 samples per packet at 25 packets per second.
- Keep sampling independent from Bluetooth: SPP callbacks and congestion may drop queued transport packets but must not stop ECG sampling.
- Use a `DataPublisher` interface on the PC side; `NullPublisher` is the default until a future WebSocket publisher is added.
- GUI serial reading, disk recording, parsing, and NeuroKit2 analysis run off the GUI main thread.

## Work breakdown

1. Complete the foundation work already listed below.
2. Milestone 1: implement MPU6050 initialization/readout, ECG ADC/GPTimer sampling, lead-off GPIO, monotonic timestamps, and non-blocking ring buffers with overflow counters.
3. Milestone 2: extend protocol batching for `ECG_BATCH`, `IMU_BATCH`, and `DEVICE_STATUS`; update C/Python implementations, docs, golden vectors, and tests.
4. Milestone 3: add Bluetooth Classic SPP acceptor using the ESP-IDF v6.0.2 `bt_spp_acceptor` pattern; maintain a separate TX queue and write/congestion state.
5. Milestone 4: add OLED status display pages at 2 Hz maximum, outside sampling and SPP callbacks.
6. Milestone 5: add Windows PC serial parser, COM enumeration, raw binary recording, and independent ring buffers.
7. Milestone 6: add ECG GUI views with raw ECG, cleaned ECG, R peaks, HR, RR, SQI, lead-off, packet loss, and CRC errors.
8. Milestone 7: add IMU GUI values, complementary-filter attitude, reset orientation, mounting transform, yaw-drift warning, and 3D box display.
9. Milestone 8: add `docs/TODO.md` webpage placeholder and `DataPublisher` / `NullPublisher` extension point.
10. Run doctor, build, size, PC tests, golden-vector checks, and `git diff --check`.
11. Update this plan with discoveries and results.

## Validation

Exact commands:

```powershell
. 'C:\Espressif\tools\Microsoft.v6.0.2.PowerShell_profile.ps1'
.\tools\project.ps1 doctor
.\tools\project.ps1 build
.\tools\project.ps1 size
.\tools\project.ps1 pc-test
git diff --check
```

This round does not flash, open monitor, or perform a body-connected test unless separately requested.

## Risks and rollback

- ESP-IDF v6.0.2 driver APIs may differ from recalled examples; resolve by compiling and adjusting includes/config within the current scope.
- Python dependency installation may require network; keep parser/protocol tests runnable without GUI extras.
- Bluetooth Classic SPP configuration may increase firmware image size and expose Kconfig dependency drift; rollback is to disable the SPP component and keep UART/PC parser work independent.
- PC GUI dependencies may not be installed locally; tests should cover parser/protocol without requiring GUI libraries at import time.
- NeuroKit2 may be absent or slow; keep raw ECG display and storage functional without it, and run analysis on a worker cadence.
- If firmware build changes produce unexpected partition or flash defaults, rollback is file-level removal of the new V0 source files and restoration of the previous foundation CMake/main files.

## Progress

- [x] Read AGENTS, PLANS, contributing notes, startup prompt, and V0 implementation plan.
- [x] Confirmed project PowerShell Skill package layout.
- [x] Created execution branch `feat/v0-foundation`.
- [x] Created this ExecPlan.
- [x] Implement firmware V0 foundation.
- [x] Implement protocol Python package and tests.
- [x] Run required foundation validation.
- [x] Milestone 1: sensor sampling code.
- [x] Milestone 2: protocol batching code, docs, golden vectors, and pytest.
- [x] Milestone 3: Bluetooth Classic SPP code.
- [x] Milestone 4: OLED status code.
- [x] Milestone 5: Windows PC serial parser and recorder code.
- [x] Milestone 6: ECG GUI code.
- [x] Milestone 7: IMU attitude GUI code.
- [x] Milestone 8: webpage placeholder and publisher interface.
- [x] Final validation for this round.

## Discoveries

- The V0 plan file is UTF-8; PowerShell output must force UTF-8 to avoid mojibake in Chinese text.
- `docs/V0_Codex_ESP-IDF_Classic_ESP32_Software_Plan.md` already has a small uncommitted user edit around the target-chip wording.
- ESP-IDF v6.0.2 requires `esp_driver_gpio` in the main component requirements when `board_config.h` includes `driver/gpio.h`; the first build failed until this dependency was added.
- Bluetooth Kconfig defaults produced unknown-symbol warnings while F0 used a minimal build without the `bt` component. SPP implementation must add the real `bt` dependency before enabling those defaults.
- `idf.py size` still triggers `PermissionError: [WinError 5]` in the managed sandbox. Running the same wrapper outside the sandbox succeeds.
- The original `pc-setup` path did not check native command exit codes, so a failed venv/pip setup could appear successful. `tools/project.ps1` now checks native exit codes and `pc-test` can fall back to global Python with `PYTHONPATH=src` when the local venv is absent or incomplete.
- Classic Bluetooth SPP pulls in a large Bluedroid build the first time it is enabled. The first full SPP build took substantially longer than the foundation build, then incremental builds became short.
- EIM can fail to initialize logging or hide build output in some Codex-managed PowerShell sessions. `tools/project.ps1` now has a narrow local Ninja fallback for `build` and `size`, using the existing ESP-IDF v6.0.2 tool directories and only when the normal `idf.py`/EIM path fails.
- 2026-07-15 hardware smoke: direct `idf.py -C firmware -p COM18 flash monitor` flashed successfully but monitor output crashed under the default Windows GBK console codec. Retrying with `PYTHONIOENCODING=utf-8`, `PYTHONUTF8=1`, and a bounded hidden monitor process captured the full boot log.
- The hardware smoke confirmed `ESP32-D0WD-V3` revision v3.0, 4 MB flash, the custom partition table, firmware version `0.1.0`, protocol V0 golden self-test PASS, and app startup completion.
- The first startup I2C scan completed but found `0` devices.
- A later 2026-07-15 rescan with corrected wiring found both expected I2C devices: OLED `0x3C` and MPU6050 `0x68`.
- 2026-07-15 first SPP/OLED hardware run booted and started SPP, but IMU initialization failed once and the offline IMU task kept retrying I2C reads at 50 Hz. This starved OLED refresh and flooded logs.
- The fix was to retry IMU initialization only once per second while offline, start OLED before sampling, lower I2C to 50 kHz for bench wiring, and use millisecond I2C transfer timeouts instead of FreeRTOS tick-converted values.
- The IMU at address `0x68` reports `WHO_AM_I=0x72`, so firmware now treats `0x68`, `0x70`, `0x71`, and `0x72` as MPU6050-compatible IDs for the V0 raw six-axis path.
- After the fix, hardware boot logs show OLED `0x3C` ready, SPP acceptor started as `SmartCollar-V0`, MPU-compatible `WHO_AM_I=0x72`, IMU configured for 50 Hz, packet task started, and no I2C timeout during the captured startup window.
- OLED `ERR` originally included `disconnected_drop_count`, so it climbed by the normal packet production rate while Bluetooth was waiting for a PC connection. That count is now kept in `transport_drop_count`; OLED/protocol `error_count` only aggregates missed sampling, ADC/I2C errors, ring overflows, SPP queue overflow, and SPP write errors.
- OLED page switching is intentionally slower than refresh: the status task refreshes once per second and changes pages every 3 seconds.
- The first SPP implementation set the Classic BT device name and discoverable/connectable scan mode in `ESP_SPP_INIT_EVT` and ignored return values. It now follows the ESP-IDF v6.0.2 `bt_spp_acceptor` ordering more closely: start the SPP server first, set device name and scan mode after `ESP_SPP_START_EVT`, configure SSP/legacy PIN handling, and log BT address, pairing events, and GAP mode changes.
- A later SPP scan debug showed `esp_bt_gap_set_scan_mode()` could return success while immediate GAP profile readback was still `conn_mode=0 disc_mode=0`. The firmware now configures Classic BT EIR data, disables BT modem sleep for V0 bench debugging, repeats scan-mode setup after EIR configuration, and runs a short discovery watchdog. Captured startup then read back `conn_mode=1 disc_mode=2`, i.e. connectable and general discoverable.
- Windows and phones may show `paired` but not `connected` until an SPP/RFCOMM client opens the serial service. A pairing attempt produced `BT SSP confirm requested` and `BT authentication success`, but no `SPP client connected`; opening the Windows outgoing RFCOMM COM port produced `SPP client connected` and binary V0 packets. On this bench, `COM19` is the SmartCollar outgoing port and `COM20` is the local placeholder port.
- For the headless V0 device, Classic BT pairing now advertises no-input/no-output IO capability to avoid numeric-comparison PIN prompts. Existing host bond records may need to be removed before the new pairing behavior is visible.
- PyQtGraph's 3D widget imports `pyqtgraph.opengl`, which requires the separate `PyOpenGL` package. Without it the GUI falls back to the text `3D view requires pyqtgraph OpenGL support`; the GUI extra now declares `PyOpenGL>=3.1.7`.
- With USB still connected and AD8232 leads off, NeuroKit2 can warn that too few R peaks exist to compute rate. The PC analysis worker now treats that as an expected no-signal state and reports `need more R peaks` instead of printing repeated warnings.
- A captured Windows SPP stream showed one sequence gap immediately after opening the outgoing RFCOMM port: stale packet sequence `44` was followed by live sequence `1861`, producing `LOSS 1816` while CRC stayed `0`. The firmware now drops any pending SPP TX queue on SPP connect/disconnect and records those stale queued packets as transport drops instead of sending old data as if it were real-time.
- Board OLED `ERR` is not the GUI CRC counter. It aggregates missed ECG timer notifications, ADC/I2C errors, ring overflows, SPP queue overflow, and SPP write errors. A captured status value `status_flags=19` means `LO-`, `LO+`, and `SAMPLE_MISSED`; with CRC `0` and I2C error `0`, a small `ERR 7` points to seven accumulated sampling tick misses rather than Bluetooth corruption.
- A bounded COM18 log capture after this investigation reset the board and captured a clean boot: OLED `0x3C` and MPU `0x68` were found, SPP became connectable/discoverable, OLED initialized, MPU-compatible `WHO_AM_I=0x72` configured, and no startup I2C/write errors were logged. The capture did not expose a runtime per-counter breakdown for OLED `ERR`.
- The first OpenGL attitude view could show the grid while the `GLBoxItem` body was hard to see. The GUI now renders a centered solid `GLMeshItem` cuboid with bright edges and moves the grid below the body. The former `Reset Orientation` control is now `Calibrate Flat`, which records the current filtered roll/pitch/yaw as the level zero point.
- On this shell, `.\tools\project.ps1 flash` returned exit code `1` without visible stdout when the ESP-IDF profile was not already loaded. Dot-sourcing `C:\Espressif\tools\Microsoft.v6.0.2.PowerShell_profile.ps1` and running `idf.py -C firmware -p COM18 -b 460800 flash` succeeded.
- After the GUI connected and displayed an initial ECG waveform, NeuroKit2 could return no finite SQI values for the current analysis window. Calling NumPy mean helpers on that empty/all-NaN quality set printed `Mean of empty slice` and `invalid value encountered in scalar divide`; the PC analysis now leaves SQI unset until finite quality values exist.
- A later raw-log check showed valid SPP packets with CRC `0` and packet loss `0`, but the payload values were bad: ECG samples were all `0` with `ADC_CLIPPING` flags, and IMU six-axis samples were all `0`. This points below the PC parser/transport layer. PC analysis now reports clipped ECG windows directly, and firmware treats an all-zero 14-byte MPU6050 sample as an invalid response, marks the IMU offline, and lets the existing 1 Hz reinitialization path run.

## Result

Completed the first code pass for the shortest V0 real-time loop:

ESP32 sensor sampling -> Bluetooth Classic SPP TX queue -> Windows virtual COM reader -> Python binary parser -> ECG/IMU buffers -> PySide6/PyQtGraph GUI scaffold.

Verified:

- `idf.py -C firmware build`: passed after SPP stale-queue cleanup.
- `idf.py -C firmware size`: passed; total image size `657732` bytes and generated `smart_neckband.bin` length `0xa09c0`, with `0x15f640` bytes free in the 2 MB app partition.
- `.\tools\project.ps1 pc-test`: 16 pytest tests passed, including flat-calibration, no-finite-SQI, and clipped-ECG coverage.
- `idf.py -C firmware -p COM18 -b 460800 flash`: passed; bootloader, partition table, and app hashes verified, then the board hard-reset.
- C golden packet arrays match `docs/protocol/v0_golden_vectors.json` for ECG, IMU, and status packets.
- `git diff --check`: passed.

Not verified in this round:

- Body-connected behavior was not tested and must not be inferred from any USB bench smoke.
- The new real-time ECG, IMU, SPP, OLED, and PC GUI data path has not yet been flashed, paired, monitored, or run against live hardware.
- SPP pairing, Windows virtual COM streaming, MPU6050 live reads, OLED live pages, and GUI rendering are inferred from build/test only until hardware logs are captured.
