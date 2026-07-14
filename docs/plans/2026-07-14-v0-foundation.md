# V0 Foundation Implementation

## Goal

Implement the first Codex round from `docs/V0_Codex_ESP-IDF_Classic_ESP32_Software_Plan.md`: turn the Hello World baseline into a minimal V0 foundation that builds for classic ESP32, fixes 4 MB flash defaults, reports hardware facts at startup, scans I2C once, and adds matching C/Python packet definitions with golden-vector tests.

## Current state

- Branch: `feat/v0-foundation`.
- Baseline firmware is the official ESP-IDF Hello World example under `firmware/`.
- `sdkconfig.defaults` targets `esp32`, 4 MB flash, and custom `partitions.csv`.
- `partitions.csv` already reserves a 2 MB factory app partition and remaining SPIFFS storage.
- The project-local `powershell-command-runner` Skill is present with `agents/openai.yaml`, `core/scripts`, `core/pattern-catalog`, and `core/tests/run-smoke.ps1`.
- Existing uncommitted documentation changes in `AGENTS.md` and `docs/V0_Codex_ESP-IDF_Classic_ESP32_Software_Plan.md` are preserved.

## Scope

Included:

- Firmware board configuration header for pins, sample rates, firmware version, and flash configuration.
- Startup self-test logging for target, chip revision, core count, detected flash size, configured flash size, firmware version, heap, and partition summary.
- I2C master initialization and one-shot address scan from `0x08` through `0x77`.
- C protocol structs/constants for transport-independent packets and ECG payloads.
- Python package with matching dataclasses, CRC, encoding helpers, and golden-vector pytest coverage.
- Validation through repository wrappers: doctor, firmware build, size, and PC tests.

Excluded:

- Flashing, monitor, erase, or body-connected acquisition.
- ADC/GPTimer ECG sampler implementation.
- MPU6050 or OLED drivers beyond I2C bus scan readiness.
- UART, Bluetooth SPP, BLE, GUI, NeuroKit2 integration, and live data capture.

## Design decisions

- Keep all board pins and target sample rates centralized in `firmware/main/board_config.h`.
- Use ESP-IDF I2C master driver for a bounded boot-time scan; the scan is not placed in any high-frequency loop.
- Keep the C packet structs packed and little-endian on the wire; Python golden vectors assert byte-for-byte compatibility.
- Use CRC-16/CCITT-FALSE for packet integrity in V0 protocol helpers.
- Preserve raw ECG payload semantics; no firmware filtering or derived medical metrics are introduced in this round.

## Work breakdown

1. Create this ExecPlan and record the baseline observations.
2. Replace Hello World with a V0 startup entry point.
3. Add board configuration and protocol headers.
4. Add I2C scan implementation.
5. Add a minimal `pc_app` package and pytest protocol golden vectors.
6. Run doctor, build, size, and PC tests.
7. Update this plan with discoveries and results.

## Validation

Exact commands:

```powershell
. 'C:\Espressif\tools\Microsoft.v6.0.2.PowerShell_profile.ps1'
.\tools\project.ps1 doctor
.\tools\project.ps1 build
.\tools\project.ps1 size
.\tools\project.ps1 pc-test
```

Hardware checks are limited to build-time and startup-code readiness. This round does not flash or open a serial monitor unless separately requested.

## Risks and rollback

- ESP-IDF v6.0.2 driver APIs may differ from recalled examples; resolve by compiling and adjusting includes/config only within this first-round scope.
- Python dependency installation may require network; if the local test environment is missing, report the blocker and keep firmware validation separate.
- If firmware build changes produce unexpected partition or flash defaults, rollback is file-level removal of the new V0 source files and restoration of the Hello World CMake/main file.

## Progress

- [x] Read AGENTS, PLANS, contributing notes, startup prompt, and V0 implementation plan.
- [x] Confirmed project PowerShell Skill package layout.
- [x] Created execution branch `feat/v0-foundation`.
- [x] Created this ExecPlan.
- [x] Implement firmware V0 foundation.
- [x] Implement protocol Python package and tests.
- [x] Run required validation.

## Discoveries

- The V0 plan file is UTF-8; PowerShell output must force UTF-8 to avoid mojibake in Chinese text.
- `docs/V0_Codex_ESP-IDF_Classic_ESP32_Software_Plan.md` already has a small uncommitted user edit around the target-chip wording.
- ESP-IDF v6.0.2 requires `esp_driver_gpio` in the main component requirements when `board_config.h` includes `driver/gpio.h`; the first build failed until this dependency was added.
- Bluetooth Kconfig defaults produced unknown-symbol warnings while F0 uses a minimal build without the `bt` component. The invalid defaults were removed and left for F6, when SPP is actually added.
- `idf.py size` still triggers `PermissionError: [WinError 5]` in the managed sandbox. Running the same wrapper outside the sandbox succeeds.
- The original `pc-setup` path did not check native command exit codes, so a failed venv/pip setup could appear successful. `tools/project.ps1` now checks native exit codes and `pc-test` can fall back to global Python with `PYTHONPATH=src` when the local venv is absent or incomplete.

## Result

Completed first-round V0 foundation implementation.

Verified:

- `.\tools\project.ps1 doctor`: ESP-IDF v6.0.2, `esp32`, COM18, 4 MB expected flash.
- `.\tools\project.ps1 build`: generated `firmware/build/smart_neckband.bin`; app binary size `0x23b00`, 2 MB app partition with `0x1dc500` bytes free.
- `.\tools\project.ps1 size`: total image size `146061` bytes.
- `.\tools\project.ps1 pc-test`: 5 pytest tests passed.
- `git diff --check`: passed after validation.

Not verified in this round:

- No flash, monitor, or serial boot log capture was run.
- I2C device presence at `0x68` or `0x3C` is only implemented in startup scan code; it has not yet been observed on hardware.
- The C protocol golden self-test is compiled into firmware startup, but has not been observed at runtime because this round did not flash or monitor.
