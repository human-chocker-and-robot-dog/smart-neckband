# First Codex implementation prompt

```text
Start from the repository root. Read AGENTS.md, PLANS.md, CONTRIBUTING.md, docs/POWERSHELL_SKILL_LOCALIZATION.md, and docs/V0_Codex_ESP-IDF_Classic_ESP32_Software_Plan.md before changing files.

Before running any Windows or PowerShell command, invoke $powershell-command-runner from .agents/skills/powershell-command-runner and follow its original SKILL.md and core execution contract. If the Skill is not exposed by the UI, use the AGENTS.md fallback to read the same checked-in files. Do not install another copy and do not rewrite the helper scripts.

Confirmed hardware:
- classic ESP32, ESP-WROOM-32, dual core, revision v3.0
- ESP-IDF target esp32
- physical Flash 4 MB
- ECG input GPIO34 / ADC1_CH6
- LO- GPIO25, LO+ GPIO26
- I2C SDA GPIO21, SCL GPIO22

Hello World already runs. Do not repeat environment installation.

For this first implementation round:
1. Inspect the repository and report the current state.
2. Confirm that the complete project Skill exists, including agents/openai.yaml, core/scripts, core/pattern-catalog, and core/tests/run-smoke.ps1.
3. Create or update an ExecPlan under docs/plans/.
4. Create the minimal ESP-IDF firmware project under firmware.
5. Use the supplied sdkconfig.defaults and partitions.csv.
6. Centralize pins and sample rates in board_config.h.
7. Log target, chip revision, core count, detected Flash size, configured Flash size, and firmware version at startup.
8. Add I2C initialization and address scanning only. Do not yet implement full MPU6050 or OLED drivers.
9. Add protocol C structs and matching Python dataclasses with golden-vector tests.
10. Create a minimal Python package under pc_app with pytest.
11. Run the project doctor, firmware build, size report, and PC tests through the repository wrappers.
12. Do not flash, erase, commit, push, or perform a body-connected test.
13. Report files changed, commands run, test results, warnings, and unverified hardware assumptions.

Do not use ADC continuous mode at 500 Hz. The later ECG sampler will use GPTimer to notify a high-priority task, which calls adc_oneshot_read() on ADC1.
```
