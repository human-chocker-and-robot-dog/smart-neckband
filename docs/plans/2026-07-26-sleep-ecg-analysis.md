# Sleep ECG offline analysis and Health MCP integration

## Goal

Add a local PC workflow that imports Smart Neckband sessions or standard sleep ECG
files, runs the pinned SleepECG three-class model, persists completed results in the
Health SQLite database, and exposes a bounded read-only MCP sleep report.

## Current state

- `main` includes SleepECG `v0.5.9` as `third_party/SleepECG` and installs the base
  `sleepecg==0.5.9` wheel with GUI dependencies.
- Native recordings use `session.json`, `raw.bin`, `markers.json`, and
  `analysis.json`; `raw.bin` contains transport-independent V0 packets at 500 Hz.
- The PC GUI has live, history, comparison, microphone, Health/MCP, and webhook
  tabs. Health data is stored in `data/health/health_state.db`.
- Health MCP schema `0.3.0` exposes heart-rate, HRV, and IMU tools only.

## Scope

Included: offline WAKE/REM/NREM staging, native session/raw import, EDF/WFDB import,
single-lead selection, optional demographics, SQLite migration, one new MCP tool,
GUI workflow, an opt-in TensorFlow setup action, and an open SLPDB downloader and
converter.

Excluded: firmware changes, real-time sleep staging, medical diagnosis, arbitrary
CSV import, raw ECG storage in SQLite/MCP, and body-connected or hardware testing.

## Design decisions

- Use bundled classifier `wrn-gru-mesa-weighted` with 30-second epochs.
- Run TensorFlow only in the analysis worker and install its dependencies through
  `pc-sleep-setup`; ordinary `pc-setup` remains lightweight.
- Stream native packets into NumPy-backed storage instead of retaining one Python
  object per sample for an overnight record.
- Persist imported-record provenance, versioned analysis runs, and epochs. MCP reads
  only the latest or requested completed run and never executes SleepECG.
- Default sample data is PhysioNet SLPDB `slp03`, stored only below ignored `data/`.

## Work breakdown

1. Add dependency/setup plumbing and the offline import/analysis domain service.
2. Add Health DB migration 5, sleep persistence methods, deletion coverage, and tests.
3. Publish Health MCP contract `0.4.0` with `health.get_sleep_report` and tests.
4. Add the Sleep ECG Qt panel with background execution, cancellation, result plots,
   database settings integration, and UI tests.
5. Add SLPDB download/conversion tooling, provenance documentation, and optional
   end-to-end validation.

## Validation

- `\.\tools\project.ps1 pc-test`
- `\.\tools\project.ps1 pc-sleep-test`
- `pc_app\.venv\Scripts\python.exe -m pip check`
- Model load/prediction smoke test after `pc-sleep-setup`.
- Offscreen Qt construction and interaction tests.
- SLPDB checksum, conversion, parser, database, and MCP consistency checks.

## Risks and rollback

- TensorFlow is a large dependency and may have Keras compatibility issues; keep it
  opt-in, pin a tested range, and fail with actionable setup guidance.
- Overnight data can exceed memory if expanded into objects; use array/memmap paths
  and bounded epoch responses.
- Imported physical ECG must be transformed to V0 ADC counts only for test copies;
  record every transform parameter and quality flag and retain the original dataset.
- Schema migration is additive. Rollback is switching to the previous application;
  existing Health tables remain readable and new sleep tables are ignored.

## Progress

- [x] Merge and validate the SleepECG dependency branch on `main`.
- [x] Implement import and analysis services.
- [x] Implement database migration and MCP tool.
- [x] Implement GUI integration.
- [x] Implement sample-data tooling and complete validation.

## Discoveries

- Local `main` and the dependency branch had diverged, but the merge completed without
  conflicts and passed 210 PC tests before being pushed as commit `576eb57`.
- TensorFlow 2.21.0 and SleepECG 0.5.9 load the pinned classifier successfully on
  native Windows. The fixed smoke input produced finite probabilities with each
  epoch summing to approximately one.
- SLPDB `slp03` is a four-channel 250 Hz PSG record with one ECG channel and open
  ODC-By-1.0 access; multi-ECG-lead selection was validated with a real synthetic
  EDF round trip through edfio.
- The actual `slp03` download contained 5,400,000 ECG samples. Conversion produced
  10,800,000 valid 500 Hz V0 samples and 720 reference epochs; all fixed hashes
  matched.
- A full-record run detected 25,754 heartbeats and produced 719 model epochs. Every
  MCP epoch page matched the SQLite rows and the database summary stage counts.
- Real edfio validation exposed anonymized EDF files whose `startdate` property
  raises an exception. The importer now preserves the signal and reports an unknown
  date instead of rejecting the EDF.

## Result

The feature is complete on `feat/sleep-ecg-analysis`. It adds offline native,
EDF/BDF, and WFDB import; pinned three-class SleepECG inference; transactional
Health DB schema 5 persistence; Health MCP contract 0.4.0; the unified GUI tab;
and reproducible SLPDB conversion tooling. Validation completed with 20 focused
Sleep ECG tests, 230 full PC tests, `pip check`, Python byte-compilation, an actual
edfio multi-lead round trip, the real classifier smoke test, and a full `slp03`
database/MCP consistency run. No firmware, protocol, flash, hardware, or
body-connected behavior was changed or tested.
