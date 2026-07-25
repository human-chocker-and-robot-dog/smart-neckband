# AI Smart Collar Codex Bootstrap v4

This bundle targets the ESP32-C3 SuperMini only. The retired classic ESP32 / ESP-WROOM-32 build and Bluetooth Classic SPP backend are no longer active. The repository embeds the user's complete upstream `powershell-command-runner` Skill without replacing its `core/`.

## Copy into the repository

Copy all files into the project root while preserving directories. The result must include:

```text
AGENTS.md
.agents/skills/powershell-command-runner/SKILL.md
.agents/skills/powershell-command-runner/agents/openai.yaml
.agents/skills/powershell-command-runner/core/
```

Then:

1. Copy `config/local.example.ps1` to `config/local.ps1`.
2. Set the real COM port and ESP-IDF path if required.
3. Start Codex from the repository root.
4. Restart the Codex session after copying the Skill.
5. Check `/skills` or type `$powershell-command-runner`.
6. Run `\.\tools\project.ps1 doctor` only after the Skill has been loaded or manually routed through `AGENTS.md`.
7. Give Codex the prompt in `docs/CODEX_START_PROMPT.md`.

## PowerShell integration

The repository uses the complete files supplied by the user. No synthetic `core`, rewritten helper, or global installation is required. `AGENTS.md` requires Codex to invoke `$powershell-command-runner` before Windows shell work. If automatic Skill discovery fails, the same file manually routes Codex to the checked-in `SKILL.md` and original `core/`.

Only one normalization was applied: the UTF-8 BOM before the first `---` in `SKILL.md` was removed so frontmatter parsers see YAML at byte zero. The Skill body, metadata, `core`, scripts, patterns, tests, and installation metadata were otherwise preserved.

## Hardware/software baseline

- ESP32-C3 SuperMini, single-core RISC-V, observed revision v1.1;
- target `esp32c3`;
- physical Flash 4 MB;
- ECG input GPIO0 / ADC1_CH0;
- LO- GPIO3, LO+ GPIO10;
- I2C SDA GPIO6, SCL GPIO7;
- INMP441 BCLK GPIO4, WS GPIO5, SD GPIO20;
- ECG 500 Hz via GPTimer notification plus ADC oneshot task;
- encrypted BLE GATT is the production wireless transport;
- native USB serial is reserved for electronics-only bench debugging;
- raw ECG remains unfiltered in the primary firmware stream.

See [the wiring and verification guide](docs/hardware/esp32c3-supermini-wiring.md) before connecting sensors to a specific SuperMini clone.

Validate the only supported target explicitly:

```powershell
.\tools\project.ps1 build -Target esp32c3
.\tools\project.ps1 size -Target esp32c3
```

These commands build the unified ECG + IMU + OLED + Hi ESP + MIC1 firmware in
`firmware/build-c3-unified`. Use `-SensorsOnly` only as a diagnostic
rollback. The unified partition table must be fully flashed before first use;
flashing still requires explicit authorization.

## GitHub remote

This local repository is attached to:

```text
https://github.com/human-chocker-and-robot-dog/smart-neckband.git
```

Use `origin` for that remote. After Codex creates a validated Conventional Commit, it should push the committed branch to `origin` and set upstream when needed, unless the user explicitly says not to push.

## SleepECG dependency

The upstream [SleepECG](https://github.com/cbrnr/sleepecg) source is pinned to
release `v0.5.9` as the `third_party/SleepECG` Git submodule. Initialize it when
cloning or updating this repository:

```powershell
git submodule update --init --recursive
```

The normal PC setup installs the matching `sleepecg==0.5.9` Windows wheel as
part of the GUI dependencies:

```powershell
.\tools\project.ps1 pc-setup
```

The wheel is used for normal setup because an editable install from the cloned
source requires Microsoft Visual C++ 14 or newer to compile SleepECG's native
heartbeat-detection extension.

For offline Sleep ECG staging, install the opt-in TensorFlow/WFDB/EDF dependencies
with `.\tools\project.ps1 pc-sleep-setup`, then use the **Sleep ECG** tab in the
unified GUI. See [the Sleep ECG guide](docs/sleep-ecg.md) for supported formats,
the open PhysioNet fixture, database behavior, MCP access, and limitations.

## PC Agent Webhook

The PC GUI includes a **Webhook** tab for submitting durable user-text instructions to an Agent Webhook Gateway and receiving de-duplicated final reply callbacks. The optional device gate enables ordinary sends only after the ESP32-C3 connection reaches `RECEIVING`; the HTTP integration does not change the BLE firmware protocol.

See [the PC Agent Webhook guide](docs/pc-agent-webhook.md) for same-PC setup, remote-LAN setup, callback configuration, retry behavior, security limits, and troubleshooting.

## ESP32-C3 unified microphone path

The default firmware uses INMP441 audio, the official local `Hi ESP` WakeNet
model, BLE MIC1 ADPCM audio, PC-side Volcengine streaming ASR and threshold VAD,
then the same durable Agent Webhook queue used by manual text. The main GUI owns
the single BLE connection and receives both V0 sensors and MIC1. See
[the Voice setup and safety guide](docs/voice-wake-asr.md).
