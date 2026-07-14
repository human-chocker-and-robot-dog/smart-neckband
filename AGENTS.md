# AI Smart Collar Repository Instructions

## 1. Scope and authority

This file defines the durable engineering rules for this repository. Read it before editing code, running commands, changing configuration, or proposing hardware behavior.

Files closer to the current working directory may add stricter rules, but must not weaken the safety, Git, raw-data, or validation rules defined here.

## 2. Project-local PowerShell Skill

This repository contains the complete upstream project-scoped Skill at:

```text
.agents/skills/powershell-command-runner/
```

It includes the original `SKILL.md`, `agents/openai.yaml`, `core/execution-contract.md`, pattern catalog, helper scripts, failure schema, and smoke tests. Do not install or reconstruct a separate global copy for this repository.

### 2.1 Mandatory invocation

Whenever operating on Windows or PowerShell, and before any of the following actions, invoke `$powershell-command-runner` and follow its instructions:

- running a shell command;
- inspecting or modifying files through PowerShell;
- invoking Git, Python, ESP-IDF, EIM, CMake, Ninja, serial tools, or another external CLI;
- creating, reviewing, or running a `.ps1` script;
- handling Windows paths, spaces, non-ASCII names, quoting, archives, encoding, COM ports, or USB devices;
- retrying or diagnosing a failed command.

For normal read-only commands, keep execution lightweight as the Skill directs. For high-risk, destructive, or diagnostic work, first read the exact upstream files requested by the Skill, beginning with:

```text
.agents/skills/powershell-command-runner/core/execution-contract.md
```

Then read only the relevant file under `core/pattern-catalog/` and use the original helper scripts under `core/scripts/` when applicable.

### 2.2 Discovery fallback

If Codex does not expose the project Skill in `/skills` or the `$` selector, do not search for a replacement and do not claim it was invoked. Treat this section as a manual router to the same checked-in upstream files:

1. Read `.agents/skills/powershell-command-runner/SKILL.md`.
2. Apply its risk classification.
3. For high-risk, destructive, or diagnostic work, read `core/execution-contract.md` and the smallest matching pattern file.
4. Use the original helper scripts without copying or rewriting them.
5. If a required file is missing, stop and report the exact missing path.

The checked-in Skill is the source of truth for Windows command execution. Do not duplicate its detailed rules elsewhere in this repository.

### 2.3 Repository-specific limits

The Skill does not override project permissions. The agent may build, inspect, lint, and test automatically, but flashing, erasing Flash, changing eFuses, destructive Git operations, and body-connected acquisition still require explicit user instruction.

## 3. Confirmed hardware baseline

- Chip family: classic ESP32, not ESP32-S3.
- Module: ESP-WROOM-32.
- CPU: dual-core.
- Chip revision: v3.0.
- Radio: Wi-Fi + Bluetooth Classic + BLE.
- Physical flash: 4 MB.
- ESP-IDF target: `esp32`.
- Current Hello World was configured as 2 MB; this project must use 4 MB defaults.
- No PSRAM is assumed.

## 4. Pin assignment

Keep all pins centralized in one board configuration header.

- ECG ADC input: GPIO34, ADC1_CH6.
- AD8232 LO-: GPIO25.
- AD8232 LO+: GPIO26.
- I2C SDA: GPIO21.
- I2C SCL: GPIO22.
- AD8232 SDN: tied to 3.3 V in V0 hardware.
- MPU6050 expected address: `0x68`.
- OLED expected address: usually `0x3C`, but scan and report the actual address.

Do not move ECG to ADC2 pins. ADC2 is shared with Wi-Fi and creates avoidable conflicts. GPIO34 is input-only, which is suitable for AD8232 OUTPUT.

## 5. Sampling architecture

- Target ECG output rate: 500 samples per second.
- Target IMU rate: 50 samples per second.
- OLED refresh rate: at most 2 Hz.
- The primary ECG stream is raw ADC counts.
- Use GPTimer at 2 ms intervals to notify a high-priority sampling task.
- Perform `adc_oneshot_read()` from the sampling task, not from the ISR.
- Timestamp every sample or packet with the same monotonic device clock.
- Track timer overruns, missed notifications, queue overflow, transport overflow, clipping, and lead-off state.

Do not configure the classic ESP32 ADC continuous/DMA driver at 500 Hz. Its continuous driver is intended for substantially higher sampling frequencies. If a future implementation uses high-rate DMA plus decimation, treat that as a separate experiment with explicit validation.

## 6. ECG data integrity rules

- Never digitally filter the primary transmitted ECG stream in firmware.
- Never discard samples merely because they look noisy.
- Never replace raw samples with BPM, R peaks, or a smoothed waveform.
- Any local preview calculation must use a separate branch and must not mutate the primary raw buffer.
- Every packet must include packet sequence, first sample index, sample rate, monotonic timestamp, flags, and CRC.
- Preserve bad, clipped, lead-off, and overflow periods with status flags.
- Formal filtering, R-peak detection, RR, HR, HRV, and SQI belong to the PC application using NeuroKit2.
- Do not implement medical diagnosis claims.

## 7. Transport plan

Implement transports behind a common interface.

1. UART over USB for electronic bench debugging only.
2. Bluetooth Classic SPP as the first wireless V0 transport for Windows.
3. BLE GATT later, while keeping the binary protocol unchanged.

The current ESP32 supports Bluetooth Classic, so SPP is allowed. Future ESP32-C6 hardware will not support Classic Bluetooth, so application logic must not depend directly on SPP-specific APIs.

## 8. OLED rules

The 0.91-inch OLED is a status panel, not an ECG monitor.

Display only compact state such as:

- RUN / STOP
- 500 Hz sampling status
- SPP or BLE connection
- lead-off state
- ADC clipping
- queue or buffer high-water mark
- IMU online state
- error count
- optional HR and SQI written back by the PC

Do not render a continuous ECG waveform in V0.

## 9. Safety rules

- USB debugging is for electronics testing without body electrodes.
- Human ECG acquisition must use an independent battery and wireless transport.
- Do not connect desktop USB, wall power, a charging power bank, or grounded bench instruments while electrodes are attached to a person.
- Do not claim that body-connected behavior was validated unless actual battery-powered wireless test logs were supplied.
- Stop and flag any instruction that weakens these constraints.

## 10. Required workflow before editing

Before modifying files:

1. Read this file and any nearer `AGENTS.md`.
2. Run `git status --short --branch`.
3. Inspect the relevant source, tests, and documentation.
4. State the intended scope internally and avoid unrelated refactors.
5. For work expected to exceed one focused change, create or update an ExecPlan under `docs/plans/` according to `PLANS.md`.

Preserve user changes. Do not overwrite unrelated modifications.

## 11. Git policy

- `main` must remain buildable.
- Create branches named `feat/<topic>`, `fix/<topic>`, `docs/<topic>`, `test/<topic>`, or `chore/<topic>` for non-trivial work.
- Use Conventional Commits, for example `feat(firmware): add GPTimer ECG sampler`.
- Make small commits with one coherent purpose.
- Do not commit generated build directories, local ports, virtual environments, captured ECG data, or secrets.
- Commit `sdkconfig.defaults`, custom partition tables, protocol definitions, lock files, tests, and documentation.
- Do not commit or push unless the user explicitly requests it.
- Never use destructive Git commands such as `reset --hard`, `clean -fd`, force push, or history rewriting without explicit user approval.
- Do not amend a commit unless explicitly requested.

## 12. Validation requirements

After firmware changes:

```powershell
.\tools\project.ps1 build
.\tools\project.ps1 size
```

After PC application changes:

```powershell
.\tools\project.ps1 pc-test
```

After protocol changes:

- build firmware,
- run Python tests,
- update protocol documentation,
- verify shared golden packet vectors in C and Python.

Do not report a task as complete if required validation was not run. Clearly distinguish:

- verified by build or test,
- verified on actual hardware,
- inferred but not yet verified.

## 13. Flashing and hardware access

The agent may build, inspect, lint, and test automatically.

The following require explicit user instruction:

- flash firmware,
- erase flash,
- change eFuses,
- change partition layout on an existing device,
- open a serial monitor that may block other tools,
- perform body-connected acquisition.

## 14. Documentation discipline

Update documentation in the same change when modifying:

- GPIO assignments,
- sample rates,
- packet schema,
- transport behavior,
- Flash or partition configuration,
- build commands,
- safety constraints,
- acceptance criteria.

Use exact chip names. Do not refer to this board as ESP32-S3.
