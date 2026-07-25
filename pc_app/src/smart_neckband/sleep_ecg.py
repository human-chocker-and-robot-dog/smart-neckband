from __future__ import annotations

from array import array
from dataclasses import asdict, dataclass
from datetime import datetime, time as datetime_time, timezone
import hashlib
import json
from pathlib import Path
from threading import Event
from typing import Callable, Sequence

import numpy as np

from .protocol import EcgPayload, PacketParser


MIN_RECORDING_SECONDS = 10 * 60
SLEEP_EPOCH_SECONDS = 30
SLEEP_MODEL_NAME = "wrn-gru-mesa-weighted"
SLEEP_STAGES = ("UNDEFINED", "WAKE", "REM", "NREM")
RESEARCH_DISCLAIMER = (
    "Sleep stages are research estimates derived from a single ECG lead and are not "
    "a medical diagnosis."
)


class SleepEcgError(RuntimeError):
    """Raised when a sleep ECG source cannot be imported or analyzed."""


class SleepEcgCancelled(SleepEcgError):
    """Raised when a background sleep analysis is cancelled."""


@dataclass(frozen=True, slots=True)
class SleepEcgSource:
    path: Path
    source_type: str
    source_session_id: str | None
    lead_names: tuple[str, ...]
    default_lead: str
    sample_rate_hz: float
    sample_count: int
    recording_start_time: str | None
    reference_stages: tuple[str, ...] = ()
    source_license: str | None = None
    source_url: str | None = None

    @property
    def duration_seconds(self) -> float:
        if self.sample_rate_hz <= 0:
            return 0.0
        return self.sample_count / self.sample_rate_hz


@dataclass(frozen=True, slots=True)
class SleepEcgSignal:
    source: SleepEcgSource
    lead_name: str
    samples: np.ndarray
    sample_rate_hz: float
    recording_start_time: str | None
    warnings: tuple[str, ...] = ()

    @property
    def duration_seconds(self) -> float:
        return len(self.samples) / self.sample_rate_hz


@dataclass(frozen=True, slots=True)
class SleepEcgEpoch:
    epoch_index: int
    start_offset_s: int
    stage: str
    confidence: float
    probabilities: dict[str, float]
    reference_stage: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SleepEcgSummary:
    epoch_duration_s: int
    total_epoch_count: int
    valid_epoch_count: int
    analyzed_duration_s: int
    total_sleep_time_s: int
    sleep_efficiency_percent: float | None
    sleep_onset_latency_min: float | None
    wake_after_sleep_onset_min: float | None
    awakening_count: int
    stage_transition_count: int
    mean_confidence: float | None
    stage_duration_s: dict[str, int]
    stage_percent: dict[str, float]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SleepEcgAnalysisResult:
    model_name: str
    stages_mode: str
    sleepecg_version: str
    tensorflow_version: str
    heartbeat_count: int
    demographics_complete: bool
    warnings: tuple[str, ...]
    summary: SleepEcgSummary
    epochs: tuple[SleepEcgEpoch, ...]
    completed_at: str
    disclaimer: str = RESEARCH_DISCLAIMER


@dataclass(frozen=True, slots=True)
class StoredSleepEcgAnalysis:
    sleep_record_id: str
    analysis_run_id: str
    result: SleepEcgAnalysisResult


@dataclass(frozen=True, slots=True)
class SleepEcgDemographics:
    age: int | None = None
    gender: str | None = None

    def normalized_gender(self) -> str | None:
        if self.gender is None or not self.gender.strip():
            return None
        value = self.gender.strip().lower()
        if value in {"female", "f", "woman", "0", "女"}:
            return "female"
        if value in {"male", "m", "man", "1", "男"}:
            return "male"
        raise ValueError("gender must be female or male")


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def source_sha256(path: str | Path) -> str:
    source = Path(path)
    digest = hashlib.sha256()
    if source.is_dir():
        files = [source / "session.json", source / "raw.bin"]
    elif source.is_file():
        files = [source]
    else:
        files = [source.with_suffix(".hea"), source.with_suffix(".dat")]
    for item in files:
        if not item.is_file():
            continue
        digest.update(item.name.encode("utf-8"))
        with item.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


def execute_sleep_analysis(
    *,
    store,
    wearer_id: str,
    source: SleepEcgSource,
    lead_name: str | None = None,
    demographics: SleepEcgDemographics | None = None,
    display_name: str | None = None,
    cancel_event: Event | None = None,
    progress: Callable[[int, str], None] | None = None,
) -> StoredSleepEcgAnalysis:
    demographics = demographics or SleepEcgDemographics()
    selected = lead_name or source.default_lead
    _progress(progress, 1, "Loading selected ECG lead")
    signal = load_sleep_ecg_signal(
        source,
        lead_name=selected,
        cancel_event=cancel_event,
    )
    _check_cancel(cancel_event)
    gender = demographics.normalized_gender()
    record_id = store.create_sleep_record(
        wearer_id=wearer_id,
        source_type=source.source_type,
        source_session_id=source.source_session_id,
        source_sha256=source_sha256(source.path),
        display_name=(display_name or source.source_session_id or source.path.name),
        source_name=source.path.name,
        lead_name=signal.lead_name,
        sample_rate_hz=signal.sample_rate_hz,
        sample_count=len(signal.samples),
        duration_s=signal.duration_seconds,
        recording_start_time=signal.recording_start_time,
        demographics={"age": demographics.age, "gender": gender},
        provenance={
            "source_path_name": source.path.name,
            "source_license": source.source_license,
            "source_url": source.source_url,
            "reference_epoch_count": len(source.reference_stages),
        },
    )
    run_id = store.start_sleep_analysis_run(
        sleep_record_id=record_id,
        wearer_id=wearer_id,
        model_name=SLEEP_MODEL_NAME,
        parameters={
            "epoch_duration_s": SLEEP_EPOCH_SECONDS,
            "lead_name": signal.lead_name,
            "sample_rate_hz": signal.sample_rate_hz,
            "demographics_complete": demographics.age is not None and gender is not None,
        },
    )
    try:
        result = analyze_sleep_ecg(
            signal,
            demographics=demographics,
            cancel_event=cancel_event,
            progress=progress,
        )
        store.complete_sleep_analysis_run(
            analysis_run_id=run_id,
            stages_mode=result.stages_mode,
            sleepecg_version=result.sleepecg_version,
            tensorflow_version=result.tensorflow_version,
            heartbeat_count=result.heartbeat_count,
            summary=result.summary.to_dict(),
            quality={
                "demographics_complete": result.demographics_complete,
                "warnings": list(result.warnings),
            },
            epochs=[epoch.to_dict() for epoch in result.epochs],
            completed_at=result.completed_at,
        )
    except SleepEcgCancelled as exc:
        store.fail_sleep_analysis_run(
            analysis_run_id=run_id,
            error_message=str(exc),
            cancelled=True,
        )
        raise
    except BaseException as exc:
        store.fail_sleep_analysis_run(
            analysis_run_id=run_id,
            error_message=str(exc),
        )
        raise
    return StoredSleepEcgAnalysis(
        sleep_record_id=record_id,
        analysis_run_id=run_id,
        result=result,
    )


def probe_sleep_ecg_source(path: str | Path) -> SleepEcgSource:
    source = Path(path).expanduser().resolve()
    if source.is_dir():
        raw_path = source / "raw.bin"
        if not raw_path.is_file():
            raise SleepEcgError(f"Session directory does not contain raw.bin: {source}")
        return _probe_native(raw_path, source / "session.json")
    if not source.is_file():
        raise SleepEcgError(f"Sleep ECG source does not exist: {source}")
    suffix = source.suffix.lower()
    if suffix == ".bin":
        metadata = source.with_name("session.json")
        return _probe_native(source, metadata)
    if suffix in {".edf", ".bdf"}:
        return _probe_edf(source)
    if suffix in {".hea", ".dat"} or source.with_suffix(".hea").is_file():
        return _probe_wfdb(source)
    raise SleepEcgError(
        "Unsupported Sleep ECG input. Select a session directory, raw.bin, EDF/BDF, "
        "or WFDB header/data file."
    )


def load_sleep_ecg_signal(
    source: SleepEcgSource,
    *,
    lead_name: str | None = None,
    cancel_event: Event | None = None,
) -> SleepEcgSignal:
    _check_cancel(cancel_event)
    selected = lead_name or source.default_lead
    if selected not in source.lead_names:
        raise SleepEcgError(f"Unknown ECG lead {selected!r}; choices: {source.lead_names}")
    if source.source_type in {"smart_neckband_session", "smart_neckband_raw"}:
        return _load_native(source, cancel_event=cancel_event)
    if source.source_type == "edf":
        return _load_edf(source, selected, cancel_event=cancel_event)
    if source.source_type == "wfdb":
        return _load_wfdb(source, selected, cancel_event=cancel_event)
    raise SleepEcgError(f"Unsupported source type: {source.source_type}")


def analyze_sleep_ecg(
    signal: SleepEcgSignal,
    *,
    demographics: SleepEcgDemographics | None = None,
    cancel_event: Event | None = None,
    progress: Callable[[int, str], None] | None = None,
    model_name: str = SLEEP_MODEL_NAME,
) -> SleepEcgAnalysisResult:
    if signal.duration_seconds < MIN_RECORDING_SECONDS:
        raise SleepEcgError(
            f"Sleep ECG recording is too short: {signal.duration_seconds:.1f}s; "
            f"at least {MIN_RECORDING_SECONDS}s is required."
        )
    demographics = demographics or SleepEcgDemographics()
    gender = demographics.normalized_gender()
    if demographics.age is not None and not 0 <= demographics.age <= 120:
        raise ValueError("age must be in 0..120")
    warnings = list(signal.warnings)
    if demographics.age is None:
        warnings.append("age metadata is missing; classifier confidence may be reduced")
    if gender is None:
        warnings.append("gender metadata is missing; classifier confidence may be reduced")

    _progress(progress, 5, "Preparing ECG")
    _check_cancel(cancel_event)
    values = np.asarray(signal.samples, dtype=np.float64)
    finite = np.isfinite(values)
    invalid_count = int(values.size - np.count_nonzero(finite))
    if invalid_count:
        replacement = float(np.median(values[finite])) if np.any(finite) else 0.0
        values = values.copy()
        values[~finite] = replacement
        warnings.append(
            f"{invalid_count} non-finite samples were replaced only in the analysis branch"
        )

    try:
        import sleepecg
        from sleepecg import SleepRecord, SubjectData
    except ImportError as exc:
        raise SleepEcgError(
            "SleepECG is not installed. Run '.\\tools\\project.ps1 pc-sleep-setup'."
        ) from exc

    _progress(progress, 15, "Detecting heartbeats")
    heartbeat_indices = sleepecg.detect_heartbeats(values, signal.sample_rate_hz)
    _check_cancel(cancel_event)
    heartbeat_times = np.asarray(heartbeat_indices, dtype=float) / signal.sample_rate_hz
    if heartbeat_times.size < 10:
        warnings.append("very few heartbeats were detected")

    subject_data = None
    if demographics.age is not None or gender is not None:
        subject_data = SubjectData(
            gender=None if gender is None else (0 if gender == "female" else 1),
            age=demographics.age,
        )
    record = SleepRecord(
        id=signal.source.source_session_id or signal.source.path.stem,
        sleep_stage_duration=SLEEP_EPOCH_SECONDS,
        recording_start_time=_recording_time(signal.recording_start_time),
        heartbeat_times=heartbeat_times,
        subject_data=subject_data,
    )

    _progress(progress, 45, "Loading SleepECG classifier")
    try:
        classifier = sleepecg.load_classifier(model_name, "SleepECG")
    except ImportError as exc:
        raise SleepEcgError(
            "TensorFlow sleep-model dependencies are missing. Run "
            "'.\\tools\\project.ps1 pc-sleep-setup'."
        ) from exc
    _check_cancel(cancel_event)
    _progress(progress, 60, "Predicting sleep stages")
    probabilities = np.asarray(
        sleepecg.stage(classifier, record, return_mode="prob"), dtype=float
    )
    _check_cancel(cancel_event)
    if probabilities.ndim != 2 or probabilities.shape[0] == 0:
        raise SleepEcgError(
            f"SleepECG returned an invalid probability matrix: {probabilities.shape}"
        )
    labels = _probability_labels(probabilities.shape[1])
    epochs = _epochs_from_probabilities(
        probabilities,
        labels=labels,
        reference_stages=signal.source.reference_stages,
    )
    summary = summarize_sleep_epochs(epochs)

    try:
        from importlib import metadata

        tensorflow_version = metadata.version("tensorflow")
    except Exception:
        tensorflow_version = "unknown"
    _progress(progress, 100, "Sleep analysis complete")
    return SleepEcgAnalysisResult(
        model_name=model_name,
        stages_mode=str(classifier.stages_mode),
        sleepecg_version=str(getattr(sleepecg, "__version__", "unknown")),
        tensorflow_version=tensorflow_version,
        heartbeat_count=int(heartbeat_times.size),
        demographics_complete=(demographics.age is not None and gender is not None),
        warnings=tuple(dict.fromkeys(warnings)),
        summary=summary,
        epochs=epochs,
        completed_at=utc_now_text(),
    )


def summarize_sleep_epochs(
    epochs: Sequence[SleepEcgEpoch],
) -> SleepEcgSummary:
    counts = {stage: 0 for stage in SLEEP_STAGES}
    confidences: list[float] = []
    valid: list[SleepEcgEpoch] = []
    for epoch in epochs:
        counts[epoch.stage] = counts.get(epoch.stage, 0) + 1
        if epoch.stage != "UNDEFINED":
            valid.append(epoch)
            confidences.append(epoch.confidence)
    valid_count = len(valid)
    valid_duration = valid_count * SLEEP_EPOCH_SECONDS
    sleep_count = counts.get("REM", 0) + counts.get("NREM", 0)
    total_sleep = sleep_count * SLEEP_EPOCH_SECONDS
    stage_duration = {
        stage: counts.get(stage, 0) * SLEEP_EPOCH_SECONDS for stage in SLEEP_STAGES
    }
    stage_percent = {
        stage: (
            round(100.0 * counts.get(stage, 0) / valid_count, 3)
            if valid_count and stage != "UNDEFINED"
            else 0.0
        )
        for stage in SLEEP_STAGES
    }
    sleep_indices = [
        index for index, epoch in enumerate(epochs) if epoch.stage in {"REM", "NREM"}
    ]
    sleep_onset = (
        sleep_indices[0] * SLEEP_EPOCH_SECONDS / 60.0 if sleep_indices else None
    )
    waso_epochs = 0
    awakenings = 0
    if sleep_indices:
        first, last = sleep_indices[0], sleep_indices[-1]
        previous_sleep = True
        for epoch in epochs[first : last + 1]:
            if epoch.stage == "WAKE":
                waso_epochs += 1
                if previous_sleep:
                    awakenings += 1
                previous_sleep = False
            elif epoch.stage in {"REM", "NREM"}:
                previous_sleep = True
    transitions = sum(
        1
        for previous, current in zip(valid, valid[1:])
        if previous.stage != current.stage
    )
    return SleepEcgSummary(
        epoch_duration_s=SLEEP_EPOCH_SECONDS,
        total_epoch_count=len(epochs),
        valid_epoch_count=valid_count,
        analyzed_duration_s=valid_duration,
        total_sleep_time_s=total_sleep,
        sleep_efficiency_percent=(
            round(100.0 * total_sleep / valid_duration, 3)
            if valid_duration
            else None
        ),
        sleep_onset_latency_min=(
            round(sleep_onset, 3) if sleep_onset is not None else None
        ),
        wake_after_sleep_onset_min=(
            round(waso_epochs * SLEEP_EPOCH_SECONDS / 60.0, 3)
            if sleep_indices
            else None
        ),
        awakening_count=awakenings,
        stage_transition_count=transitions,
        mean_confidence=(
            round(float(np.mean(confidences)), 6) if confidences else None
        ),
        stage_duration_s=stage_duration,
        stage_percent=stage_percent,
    )


def collapse_reference_stage(value: object) -> str:
    text = str(value).strip().upper().replace("S", "N", 1)
    if text in {"W", "WAKE"}:
        return "WAKE"
    if text in {"R", "REM"}:
        return "REM"
    if text in {"1", "2", "3", "4", "N1", "N2", "N3", "N4", "NREM"}:
        return "NREM"
    return "UNDEFINED"


def _probe_native(raw_path: Path, metadata_path: Path) -> SleepEcgSource:
    metadata: dict[str, object] = {}
    if metadata_path.is_file():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    sample_rate = float(metadata.get("ecg_sample_rate_hz", 500))
    sample_count = int(metadata.get("valid_sample_count", metadata.get("sample_count", 0)))
    if sample_count <= 0:
        sample_count, detected_rate = _native_dimensions(raw_path)
        sample_rate = detected_rate or sample_rate
    source_dir = raw_path.parent
    provenance = _load_source_metadata(source_dir / "source.json")
    return SleepEcgSource(
        path=source_dir if metadata_path.is_file() else raw_path,
        source_type=(
            "smart_neckband_session" if metadata_path.is_file() else "smart_neckband_raw"
        ),
        source_session_id=(
            str(metadata.get("session_id")) if metadata.get("session_id") else None
        ),
        lead_names=("ECG",),
        default_lead="ECG",
        sample_rate_hz=sample_rate,
        sample_count=sample_count,
        recording_start_time=(
            str(metadata.get("started_at")) if metadata.get("started_at") else None
        ),
        reference_stages=_load_reference_stages(source_dir / "sleep_reference.json"),
        source_license=(
            str(provenance.get("license") or metadata.get("source_license"))
            if provenance.get("license") or metadata.get("source_license")
            else None
        ),
        source_url=(
            str(provenance.get("url") or metadata.get("source_url"))
            if provenance.get("url") or metadata.get("source_url")
            else None
        ),
    )


def _native_dimensions(raw_path: Path) -> tuple[int, float]:
    parser = PacketParser()
    count = 0
    sample_rate = 0.0
    with raw_path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            for packet in parser.feed(chunk):
                if not parser.commit_packet(packet):
                    continue
                if isinstance(packet.payload, EcgPayload):
                    count += len(packet.payload.samples)
                    sample_rate = float(packet.payload.sample_rate_hz)
    return count, sample_rate


def _load_native(
    source: SleepEcgSource,
    *,
    cancel_event: Event | None,
) -> SleepEcgSignal:
    raw_path = source.path / "raw.bin" if source.path.is_dir() else source.path
    parser = PacketParser()
    values = array("H")
    sample_rate = source.sample_rate_hz
    with raw_path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            _check_cancel(cancel_event)
            for packet in parser.feed(chunk):
                if not parser.commit_packet(packet):
                    continue
                payload = packet.payload
                if isinstance(payload, EcgPayload):
                    if values and float(payload.sample_rate_hz) != sample_rate:
                        raise SleepEcgError("ECG sample rate changes within raw.bin")
                    sample_rate = float(payload.sample_rate_hz)
                    values.extend(payload.samples)
    valid_count = min(source.sample_count or len(values), len(values))
    samples = np.frombuffer(values, dtype=np.uint16, count=valid_count).astype(
        np.float32, copy=True
    )
    warnings: list[str] = []
    if parser.stats.crc_errors:
        warnings.append(f"raw.bin contains {parser.stats.crc_errors} CRC errors")
    if parser.stats.packets_lost:
        warnings.append(f"raw.bin sequence indicates {parser.stats.packets_lost} lost packets")
    return SleepEcgSignal(
        source=source,
        lead_name="ECG",
        samples=samples,
        sample_rate_hz=sample_rate,
        recording_start_time=source.recording_start_time,
        warnings=tuple(warnings),
    )


def _probe_edf(path: Path) -> SleepEcgSource:
    try:
        from edfio import read_edf
    except ImportError as exc:
        raise SleepEcgError(
            "EDF support is not installed. Run '.\\tools\\project.ps1 pc-sleep-setup'."
        ) from exc
    edf = read_edf(path)
    signals = list(getattr(edf, "signals", ()))
    leads = tuple(
        label
        for signal in signals
        if _is_ecg_label(label := _signal_label(signal))
    )
    if not leads:
        raise SleepEcgError(f"No ECG/EKG signal found in EDF: {path}")
    first = next(signal for signal in signals if _signal_label(signal) == leads[0])
    data = np.asarray(getattr(first, "data"))
    start = _edf_start_text(edf)
    return SleepEcgSource(
        path=path,
        source_type="edf",
        source_session_id=path.stem,
        lead_names=leads,
        default_lead=leads[0],
        sample_rate_hz=float(getattr(first, "sampling_frequency")),
        sample_count=int(data.size),
        recording_start_time=start,
        reference_stages=_load_reference_stages(path.with_name("sleep_reference.json")),
    )


def _load_edf(
    source: SleepEcgSource,
    lead_name: str,
    *,
    cancel_event: Event | None,
) -> SleepEcgSignal:
    from edfio import read_edf

    _check_cancel(cancel_event)
    edf = read_edf(source.path)
    signal = next(
        (item for item in getattr(edf, "signals", ()) if _signal_label(item) == lead_name),
        None,
    )
    if signal is None:
        raise SleepEcgError(f"EDF lead disappeared: {lead_name}")
    values = np.asarray(getattr(signal, "data"), dtype=np.float32)
    return SleepEcgSignal(
        source=source,
        lead_name=lead_name,
        samples=values,
        sample_rate_hz=float(getattr(signal, "sampling_frequency")),
        recording_start_time=source.recording_start_time,
    )


def _wfdb_base(path: Path) -> Path:
    if path.suffix.lower() in {".hea", ".dat"}:
        return path.with_suffix("")
    return path


def _probe_wfdb(path: Path) -> SleepEcgSource:
    try:
        import wfdb
    except ImportError as exc:
        raise SleepEcgError(
            "WFDB support is not installed. Run '.\\tools\\project.ps1 pc-sleep-setup'."
        ) from exc
    base = _wfdb_base(path)
    header = wfdb.rdheader(str(base))
    leads = tuple(str(name) for name in header.sig_name if _is_ecg_label(str(name)))
    if not leads:
        raise SleepEcgError(f"No ECG/EKG signal found in WFDB record: {base}")
    return SleepEcgSource(
        path=base,
        source_type="wfdb",
        source_session_id=base.name,
        lead_names=leads,
        default_lead=leads[0],
        sample_rate_hz=float(header.fs),
        sample_count=int(header.sig_len),
        recording_start_time=(
            header.base_datetime.isoformat()
            if getattr(header, "base_datetime", None) is not None
            else str(header.base_time)
            if getattr(header, "base_time", None) is not None
            else None
        ),
        reference_stages=_read_wfdb_reference(base, float(header.fs)),
        source_license=(
            "Open Data Commons Attribution License v1.0"
            if base.name.lower() == "slp03"
            else None
        ),
        source_url=(
            "https://physionet.org/content/slpdb/1.0.0/"
            if base.name.lower() == "slp03"
            else None
        ),
    )


def _load_wfdb(
    source: SleepEcgSource,
    lead_name: str,
    *,
    cancel_event: Event | None,
) -> SleepEcgSignal:
    import wfdb

    _check_cancel(cancel_event)
    header = wfdb.rdheader(str(source.path))
    channel = list(header.sig_name).index(lead_name)
    record = wfdb.rdrecord(str(source.path), channels=[channel])
    values = np.asarray(record.p_signal[:, 0], dtype=np.float32)
    return SleepEcgSignal(
        source=source,
        lead_name=lead_name,
        samples=values,
        sample_rate_hz=float(record.fs),
        recording_start_time=source.recording_start_time,
    )


def _read_wfdb_reference(base: Path, sample_rate_hz: float) -> tuple[str, ...]:
    annotation_path = Path(f"{base}.st")
    if not annotation_path.is_file():
        return ()
    try:
        import wfdb

        annotation = wfdb.rdann(str(base), "st")
    except Exception:
        return ()
    stage_by_epoch: dict[int, str] = {}
    for sample, note in zip(annotation.sample, annotation.aux_note):
        text = str(note).strip()
        if not text:
            continue
        stage_by_epoch[int(sample // (sample_rate_hz * SLEEP_EPOCH_SECONDS))] = (
            collapse_reference_stage(text[0])
        )
    if not stage_by_epoch:
        return ()
    last = max(stage_by_epoch)
    return tuple(stage_by_epoch.get(index, "UNDEFINED") for index in range(last + 1))


def _load_reference_stages(path: Path) -> tuple[str, ...]:
    if not path.is_file():
        return ()
    payload = json.loads(path.read_text(encoding="utf-8"))
    stages = payload.get("stages", []) if isinstance(payload, dict) else payload
    result: list[str] = []
    for item in stages:
        value = item.get("stage") if isinstance(item, dict) else item
        result.append(collapse_reference_stage(value))
    return tuple(result)


def _load_source_metadata(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _epochs_from_probabilities(
    probabilities: np.ndarray,
    *,
    labels: tuple[str, ...],
    reference_stages: Sequence[str],
) -> tuple[SleepEcgEpoch, ...]:
    epochs: list[SleepEcgEpoch] = []
    for index, row in enumerate(probabilities):
        if not np.all(np.isfinite(row)):
            stage = "UNDEFINED"
            confidence = 0.0
            normalized = np.zeros_like(row, dtype=float)
        else:
            total = float(np.sum(row))
            normalized = row / total if total > 0 else np.zeros_like(row, dtype=float)
            best = int(np.argmax(normalized)) if total > 0 else 0
            stage = labels[best]
            confidence = float(normalized[best]) if total > 0 else 0.0
        probability_map = {stage_name: 0.0 for stage_name in SLEEP_STAGES}
        for label, value in zip(labels, normalized):
            probability_map[label] = round(float(value), 8)
        reference = (
            collapse_reference_stage(reference_stages[index])
            if index < len(reference_stages)
            else None
        )
        epochs.append(
            SleepEcgEpoch(
                epoch_index=index,
                start_offset_s=index * SLEEP_EPOCH_SECONDS,
                stage=stage,
                confidence=round(confidence, 8),
                probabilities=probability_map,
                reference_stage=reference,
            )
        )
    return tuple(epochs)


def _probability_labels(column_count: int) -> tuple[str, ...]:
    if column_count == 4:
        return SLEEP_STAGES
    if column_count == 3:
        return ("WAKE", "REM", "NREM")
    raise SleepEcgError(
        f"Unsupported classifier probability width {column_count}; expected 3 or 4"
    )


def _recording_time(value: str | None) -> datetime_time | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timetz().replace(tzinfo=None)
    except ValueError:
        try:
            return datetime_time.fromisoformat(value)
        except ValueError:
            return None


def _signal_label(signal: object) -> str:
    for attribute in ("label", "name", "signal_label"):
        value = getattr(signal, attribute, None)
        if value is not None:
            return str(value).strip()
    return ""


def _is_ecg_label(value: str) -> bool:
    normalized = value.upper().replace(" ", "")
    return "ECG" in normalized or "EKG" in normalized


def _edf_start_text(edf: object) -> str | None:
    try:
        startdate = getattr(edf, "startdate", None)
    except (AttributeError, ValueError):
        startdate = None
    try:
        starttime = getattr(edf, "starttime", None)
    except (AttributeError, ValueError):
        starttime = None
    if startdate is not None and starttime is not None:
        try:
            return datetime.combine(startdate, starttime).isoformat()
        except TypeError:
            pass
    return str(starttime) if starttime is not None else None


def _check_cancel(cancel_event: Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise SleepEcgCancelled("Sleep ECG analysis was cancelled")


def _progress(
    callback: Callable[[int, str], None] | None,
    value: int,
    message: str,
) -> None:
    if callback is not None:
        callback(value, message)
