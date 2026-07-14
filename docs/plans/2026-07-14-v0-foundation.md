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

## Result

Completed the first code pass for the shortest V0 real-time loop:

ESP32 sensor sampling -> Bluetooth Classic SPP TX queue -> Windows virtual COM reader -> Python binary parser -> ECG/IMU buffers -> PySide6/PyQtGraph GUI scaffold.

Verified:

- `.\tools\project.ps1 build`: passed after Classic BT SPP integration.
- `.\tools\project.ps1 size`: passed.
- ESP-IDF size tool: total image size `654088` bytes; generated `smart_neckband.bin` length `654208` bytes; 2 MB app partition has `0x160480` bytes free.
- `.\tools\project.ps1 pc-test`: 12 pytest tests passed.
- C golden packet arrays match `docs/protocol/v0_golden_vectors.json` for ECG, IMU, and status packets.
- `git diff --check`: passed.

Not verified in this round:

- Body-connected behavior was not tested and must not be inferred from any USB bench smoke.
- The new real-time ECG, IMU, SPP, OLED, and PC GUI data path has not yet been flashed, paired, monitored, or run against live hardware.
- SPP pairing, Windows virtual COM streaming, MPU6050 live reads, OLED live pages, and GUI rendering are inferred from build/test only until hardware logs are captured.
