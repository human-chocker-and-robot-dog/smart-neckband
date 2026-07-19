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
- [x] Phase 2: add session metadata model, delayed recording controls, raw session capture, interrupted-save behavior, and session directory creation.
- [x] Phase 3: add action marker buttons, marker JSON persistence, and live Raw/Clean marker lines.
- [x] Phase 4: add historical session scanner, raw.bin ECG loader, static Raw/Clean ECG viewer, metadata summary, analysis summary, marker display, and loader tests.
- [x] Phase 5: add A/B session comparison, Raw/Clean mode, zero-mean y-axis mode, marker alignment lines, JSON summary export, PNG export, and export tests.
- [x] Layout refinement: move IMU attitude, 3D view, flat calibration, and ECG analysis configuration from the live page into a dedicated diagnostics page so Raw/Clean ECG plots keep the main live-page space.
- [x] Comparison refinement: replace single-plot overlay with synchronized upper/lower ECG tracks, default 10-second viewport, duration selector, horizontal scrollbar, R-peak and marker toggles, marker jump controls, current-view PNG export, and analysis CSV export.

## Discoveries

- The current ECG analysis calls `nk.ecg_clean`, `nk.ecg_peaks`, and `nk.ecg_quality` directly. It does not call `nk.ecg_process`.
- The live worker runs every 0.5 seconds on a 10-second ECG window, so the analysis windows overlap by about 9.5 seconds.
- Current HR uses physiologically plausible RR intervals from 300 ms to 2000 ms and a recent-RR median.
- Current clipping handling treats a window as clipped when at least 80 percent of ECG samples carry `ADC_CLIPPING`.
- Session recording clears a small raw chunk prebuffer when it starts waiting for the first post-countdown ECG sample, then flushes that prebuffer into `raw.bin` when the first new sample is observed. This avoids retroactively including pre-countdown bytes while reducing the chance of dropping the trigger packet.
- Live markers are currently shown as vertical lines on Raw and Clean ECG plots. Marker text labels are persisted in `markers.json` and can be rendered by the historical viewer in Phase 4.
- Historical ECG loading reuses the same V0 binary `PacketParser`, ignores IMU/status packets for the ECG viewer, and downsamples plot points for display only. The saved `raw.bin` is not modified.
- Dual-track comparison reuses the historical loader and aligns each track to its own session start in seconds. Marker alignment is visualized as colored vertical lines per track.
- The live page now prioritizes connection state, recording controls, HR/RR/SQI/lead/loss/CRC status, and Raw/Clean ECG. IMU orientation and ECG algorithm details live on the diagnostics page.
- Comparison CSV export uses UTF-8 with BOM and writes one row per ECG sample for track A and track B. It includes raw ADC, clean ECG, R-peak flags, RR/instant HR when derivable from peaks, lead-off aggregate state, marker type/label, placement metadata, wire map, and the ECG analysis method fields. `display_hr_bpm` and `sqi` remain blank because the current offline comparison loader does not reconstruct GUI-time smoothed HR or per-sample/window SQI.

## Result

Implemented through Phase 5. Live-page layout refinement and comparison CSV/readability refinement are implemented and validated.
