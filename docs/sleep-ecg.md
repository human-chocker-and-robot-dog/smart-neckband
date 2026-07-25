# Sleep ECG offline analysis

The Windows PC application can import a complete ECG recording, estimate
`WAKE / REM / NREM` stages locally with SleepECG, save the completed report to
the Health SQLite database, and expose that report through the read-only Health
MCP server.

This workflow is for research and engineering evaluation. A single-lead ECG
sleep-stage estimate is not a medical diagnosis and must not be used as a
substitute for clinical polysomnography.

## Install

Install the ordinary PC application first, then the opt-in sleep dependencies:

```powershell
.\tools\project.ps1 pc-setup
.\tools\project.ps1 pc-sleep-setup
```

The second command installs the pinned `sleepecg==0.5.9` package, TensorFlow,
WFDB, edfio, and the remaining full-model dependencies into `pc_app/.venv`.
TensorFlow is loaded only when an analysis starts, so normal GUI and MCP startup
do not pay its import cost. The installed packages are large and native Windows
TensorFlow 2.11 or newer uses the CPU; GPU execution requires a separately
configured WSL2 environment.

## Run an analysis

Start the unified PC application:

```powershell
.\tools\project.ps1 pc-gui
```

Open the **Sleep ECG** tab and select one of these sources:

- a Smart Neckband session directory containing `session.json` and `raw.bin`;
- an independent V0 `raw.bin` file;
- an EDF/BDF file;
- a WFDB record (`.hea` or `.dat`).

The importer detects ECG/EKG-labelled channels. If a file contains multiple ECG
leads, select exactly one lead for analysis. Recordings shorter than 10 minutes
are rejected. Age, sex, and recording start time are optional, but missing
demographics are recorded as model-quality warnings.

The page uses the wearer ID and database path from **Health / MCP**. An invalid
Health configuration still permits source preview but prevents a persisted
analysis. Analysis runs in a cancellable worker thread. Only a successful run is
marked completed and becomes visible through MCP; failed or cancelled runs never
expose partial epochs.

The result view includes the predicted hypnogram, an optional reference-stage
overlay, WAKE/REM/NREM probabilities, summary metrics, a per-epoch table, model
limitations, and—when reference stages exist—a confusion matrix and Cohen's
kappa. No medical accuracy threshold is applied.

## Storage and MCP

The Health database schema version is 5. It stores source provenance, analysis
metadata, summaries, quality warnings, and 30-second epoch probabilities in:

```text
health_sleep_records
health_sleep_analysis_runs
health_sleep_epochs
```

Raw or cleaned ECG samples are not stored in SQLite and are never returned by
MCP. Sleep reports are retained independently of the seven-day live-metric
cleanup policy. Wearer deletion cascades through sleep records, runs, and epochs.

Health MCP contract `0.4.0` adds:

```text
health.get_sleep_report
```

With no `sleep_record_id`, it returns the configured wearer's latest completed
report. Set `include_epochs=true` to read the timeline in pages. `epoch_limit`
defaults to the contract value and cannot exceed 240.

Example input:

```json
{
  "sleep_record_id": "optional-record-id",
  "include_epochs": true,
  "epoch_offset": 0,
  "epoch_limit": 240
}
```

See [the Health MCP guide](health-mcp.md) and
[the machine-readable contract](specs/health-mcp-v0.4.contract.json).

## Open test data

Prepare the reproducible PhysioNet fixture:

```powershell
.\tools\project.ps1 pc-sleep-data
```

The command downloads `slp03.hea`, `slp03.dat`, `slp03.st`, and `slp03.ecg`,
checks their fixed SHA-256 digests, selects the ECG lead from the four-channel
PSG record, resamples it from 250 Hz to 500 Hz, maps it to 12-bit ADC counts, and
encodes V0 packets with CRC and historical/quality flags. It writes the source
files below `data/sleep_datasets/` and the converted session below
`data/sessions/`; both paths are ignored by Git.

Dataset citation:

- MIT-BIH Polysomnographic Database, record `slp03`
- DOI: `10.13026/C23K5S`
- License: Open Data Commons Attribution License v1.0
- Source: <https://physionet.org/content/slpdb/1.0.0/>

The repository commits only the downloader/converter, expected checksums, and
this citation. It does not commit the ECG files.

## Validation

```powershell
.\tools\project.ps1 pc-sleep-test
.\tools\project.ps1 pc-test
.\pc_app\.venv\Scripts\python.exe -m pip check
```

No firmware build, flashing, serial monitor, hardware access, or body-connected
acquisition is part of this offline feature.
