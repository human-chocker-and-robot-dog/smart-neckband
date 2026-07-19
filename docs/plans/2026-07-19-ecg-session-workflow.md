# ECG Session Workflow and Dual-Track Comparison

## Goal

Add the minimum usable ECG experiment workflow to the existing V0 PC application:

```text
connect device -> view live ECG -> delayed recording -> action markers
-> saved session -> historical review -> two-session comparison -> export
```

The workflow supports comparing ECG electrode placements on the neck. It must preserve raw ECG as the source of truth and must not move ECG filtering, R-peak detection, HR, RR, HRV, or SQI into firmware.

## Current State

- Branch: `feat/v0-foundation`.
- Hardware and project status are summarized in `HANDOFF.md`.
- The PC app already receives Bluetooth Classic SPP data, parses the V0 binary protocol, records raw binary files, buffers ECG/IMU data, runs NeuroKit2 analysis off the GUI thread, and shows raw/clean ECG plus IMU attitude.
- The current implementation does not yet have formal experiment sessions, marker persistence, history browsing, or two-track comparison.
- The Live Beta web branch is separate and must not be merged into this work unless explicitly requested.

## Scope

Included:

- Read-only ECG analysis information in the GUI.
- Chinese connection and data-receive status.
- Experiment session metadata and delayed recording.
- Marker creation tied to ECG sample index.
- Session file structure under `pc_app/data/sessions/`.
- Historical session listing and static waveform viewing.
- Two-session waveform comparison.
- PNG and JSON summary export.

Excluded:

- Firmware ECG sampling changes.
- ESP32 flashing, erase, eFuse changes, or body-connected acquisition.
- New ECG algorithms or user-editable algorithm parameters.
- IMU feature repair.
- Huawei watch import, cloud upload, webpage sync, or Live Beta branch merging.
- Video/audio playback, editing, pause, speed controls, or more than two simultaneous ECG tracks.

## Design Decisions

- Reuse the existing PC-side analysis path for live and historical ECG analysis.
- Keep `raw.bin` as unmodified firmware packet bytes.
- Store metadata, markers, and analysis summaries as JSON files alongside `raw.bin`.
- Bind markers primarily to ECG `sample_index`; PC click time is secondary metadata.
- Use Chinese for user-facing GUI text while preserving accepted abbreviations such as ECG, HR, RR, SQI, CRC, COM, Raw, Clean, and NeuroKit2.
- Keep long-running parsing, analysis, and export off the GUI thread.

## Work Breakdown

1. Show actual ECG analysis configuration and Chinese connection state in the live GUI.
2. Add session metadata, delayed start, start/stop recording, interrupted-save behavior, and session directory creation.
3. Add action marker buttons, marker JSON persistence, and marker lines on live waveforms.
4. Add historical session list, static waveform viewer, Raw/Clean switch, marker display, metadata, and analysis summary.
5. Add two-track comparison with A/B session selection, synchronized x-range, marker alignment, y-axis modes, and PNG/JSON export.

## Validation

PC-side validation:

```powershell
.\tools\project.ps1 pc-test
git diff --check
```

Firmware build/size is not required unless firmware files change.

## Risks And Rollback

- Large sessions may contain millions of ECG samples; static viewers must downsample or clip to view instead of rendering everything synchronously.
- Corrupt `raw.bin` or unsupported schema versions must produce Chinese errors and must not close the application.
- If session recording interacts badly with live raw logging, rollback is to disable session recording while leaving the existing live parser and raw recorder intact.
- HR/SQI from historical records must stay reproducible by using the same analysis code as live mode.

## Progress

- [x] Read AGENTS, HANDOFF, PowerShell Skill, and the user-supplied implementation plan.
- [x] Created this ExecPlan.
- [x] Phase 1: add read-only ECG analysis info object.
- [x] Phase 1: add serial runtime status and Chinese connection-state mapping.
- [x] Phase 1: add live GUI connection status and analysis information panel.
- [ ] Phase 2: session recording.
- [ ] Phase 3: action markers.
- [ ] Phase 4: historical session viewer.
- [ ] Phase 5: dual-track comparison and export.

## Discoveries

- The current ECG analysis calls `nk.ecg_clean`, `nk.ecg_peaks`, and `nk.ecg_quality` directly. It does not call `nk.ecg_process`.
- The live worker runs every 0.5 seconds on a 10-second ECG window, so the analysis windows overlap by about 9.5 seconds.
- Current HR uses physiologically plausible RR intervals from 300 ms to 2000 ms and a recent-RR median.
- Current clipping handling treats a window as clipped when at least 80 percent of ECG samples carry `ADC_CLIPPING`.

## Result

In progress. Phase 1 is implemented pending validation and commit.
