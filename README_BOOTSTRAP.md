# AI Smart Collar Codex Bootstrap v4

This bundle targets the classic ESP32 / ESP-WROOM-32 board and now embeds the user's complete upstream `powershell-command-runner` Skill without replacing its `core/`.

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

- classic ESP32 / ESP-WROOM-32, dual core, revision v3.0;
- target `esp32`;
- physical Flash 4 MB;
- ECG input GPIO34 / ADC1_CH6;
- LO- GPIO25, LO+ GPIO26;
- I2C SDA GPIO21, SCL GPIO22;
- ECG 500 Hz via GPTimer notification plus ADC oneshot task;
- Bluetooth Classic SPP first, BLE later;
- raw ECG remains unfiltered in the primary firmware stream.
