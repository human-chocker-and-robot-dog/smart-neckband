from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Iterable

from .buffers import EcgSample
from .analysis import get_ecg_analysis_info
from .protocol import ECG_SAMPLE_RATE_HZ, FLAG_LO_MINUS, FLAG_LO_PLUS, EcgPayload, PacketParser
from .sessions import SCHEMA_VERSION, SessionMarker, SessionMetadata, read_session_metadata


@dataclass(frozen=True, slots=True)
class SessionRecord:
    session_dir: Path
    metadata: SessionMetadata


@dataclass(frozen=True, slots=True)
class HistoryEcgData:
    samples: tuple[EcgSample, ...]
    raw_values: tuple[float, ...]
    clean_values: tuple[float, ...]
    r_peak_indices: tuple[int, ...]
    message: str


@dataclass(frozen=True, slots=True)
class ComparisonTrack:
    label: str
    record: SessionRecord
    data: HistoryEcgData
    markers: tuple[SessionMarker, ...]


@dataclass(frozen=True, slots=True)
class ComparisonViewport:
    start_seconds: float
    duration_seconds: float | None


CSV_FIELDNAMES: tuple[str, ...] = (
    "schema_version",
    "exported_at",
    "track",
    "session_id",
    "session_name",
    "placement_id",
    "placement_name",
    "wire_map",
    "sample_rate_hz",
    "sample_index",
    "time_from_session_start_s",
    "time_from_alignment_point_s",
    "raw_adc",
    "clean_ecg",
    "rpeak_detected",
    "rpeak_accepted",
    "rr_ms",
    "instant_hr_bpm",
    "display_hr_bpm",
    "sqi",
    "lead_off",
    "marker_type",
    "marker_label",
    "analysis_library",
    "analysis_library_version",
    "clean_method",
    "peak_method",
    "quality_method",
)


def list_session_records(base_dir: Path) -> tuple[SessionRecord, ...]:
    records: list[SessionRecord] = []
    if not base_dir.exists():
        return ()
    for metadata_path in base_dir.glob("*/*/session.json"):
        try:
            metadata = read_session_metadata(metadata_path)
        except Exception:
            continue
        records.append(SessionRecord(session_dir=metadata_path.parent, metadata=metadata))
    return tuple(
        sorted(
            records,
            key=lambda record: record.metadata.started_at or record.metadata.ended_at or record.metadata.session_id,
            reverse=True,
        )
    )


def load_session_markers(session_dir: Path) -> tuple[SessionMarker, ...]:
    marker_path = session_dir / "markers.json"
    if not marker_path.exists():
        return ()
    data = json.loads(marker_path.read_text(encoding="utf-8"))
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"不支持的 markers schema_version: {data.get('schema_version')}")
    return tuple(SessionMarker(**marker) for marker in data.get("markers", []))


def load_session_analysis_summary(session_dir: Path) -> dict[str, object]:
    analysis_path = session_dir / "analysis.json"
    if not analysis_path.exists():
        return {}
    return json.loads(analysis_path.read_text(encoding="utf-8"))


def load_session_ecg_samples(session_dir: Path) -> tuple[EcgSample, ...]:
    raw_path = session_dir / "raw.bin"
    if not raw_path.exists():
        return ()
    parser = PacketParser()
    samples: list[EcgSample] = []
    metadata_path = session_dir / "session.json"
    expected_count = (
        read_session_metadata(metadata_path).sample_count
        if metadata_path.is_file() and (session_dir / "source.json").is_file()
        else None
    )
    with raw_path.open("rb") as raw_file:
        while chunk := raw_file.read(64 * 1024):
            for packet in parser.feed(chunk):
                payload = packet.payload
                if not isinstance(payload, EcgPayload):
                    continue
                sample_period_us = int(1_000_000 / payload.sample_rate_hz)
                for offset, raw_adc in enumerate(payload.samples):
                    if expected_count is not None and len(samples) >= expected_count:
                        return tuple(samples)
                    samples.append(
                        EcgSample(
                            sample_index=payload.first_sample_index + offset,
                            timestamp_us=packet.header.timestamp_us + (offset * sample_period_us),
                            raw_adc=raw_adc,
                            flags=payload.flags,
                        )
                    )
    return tuple(samples)


def analyze_history_ecg(samples: tuple[EcgSample, ...]) -> HistoryEcgData:
    raw_values = tuple(float(sample.raw_adc) for sample in samples)
    if len(raw_values) < ECG_SAMPLE_RATE_HZ:
        return HistoryEcgData(
            samples=samples,
            raw_values=raw_values,
            clean_values=raw_values,
            r_peak_indices=(),
            message="少于 1 秒 ECG，未运行 NeuroKit2",
        )
    try:
        import neurokit2 as nk
        import numpy as np
    except ImportError:
        return HistoryEcgData(
            samples=samples,
            raw_values=raw_values,
            clean_values=raw_values,
            r_peak_indices=(),
            message="未安装 NeuroKit2 / NumPy，显示原始 ECG",
        )

    raw_array = np.asarray(raw_values, dtype=float)
    try:
        clean_array = nk.ecg_clean(raw_array, sampling_rate=ECG_SAMPLE_RATE_HZ)
        _, peak_info = nk.ecg_peaks(clean_array, sampling_rate=ECG_SAMPLE_RATE_HZ)
    except Exception as exc:
        return HistoryEcgData(
            samples=samples,
            raw_values=raw_values,
            clean_values=raw_values,
            r_peak_indices=(),
            message=f"历史 ECG 分析失败：{exc}",
        )
    return HistoryEcgData(
        samples=samples,
        raw_values=raw_values,
        clean_values=tuple(float(value) for value in clean_array),
        r_peak_indices=tuple(int(index) for index in peak_info.get("ECG_R_Peaks", [])),
        message="ok",
    )


def downsample_xy(
    x_values: Iterable[float],
    y_values: Iterable[float],
    *,
    max_points: int = 10_000,
) -> tuple[list[float], list[float]]:
    x_list = list(x_values)
    y_list = list(y_values)
    if len(x_list) <= max_points:
        return x_list, y_list
    step = max(1, len(x_list) // max_points)
    return x_list[::step], y_list[::step]


def session_duration_seconds(data: HistoryEcgData) -> float:
    if not data.samples:
        return 0.0
    return len(data.samples) / float(ECG_SAMPLE_RATE_HZ)


def clamp_viewport(
    viewport: ComparisonViewport,
    *,
    max_duration_seconds: float,
    min_duration_seconds: float = 2.0,
) -> ComparisonViewport:
    if max_duration_seconds <= 0.0:
        return ComparisonViewport(0.0, viewport.duration_seconds)
    if viewport.duration_seconds is None:
        return ComparisonViewport(0.0, None)
    duration = min(max(viewport.duration_seconds, min_duration_seconds), max_duration_seconds)
    max_start = max(0.0, max_duration_seconds - duration)
    start = min(max(0.0, viewport.start_seconds), max_start)
    return ComparisonViewport(start, duration)


def visible_sample_slice(
    data: HistoryEcgData,
    viewport: ComparisonViewport,
) -> slice:
    if not data.samples:
        return slice(0, 0)
    if viewport.duration_seconds is None:
        return slice(0, len(data.samples))
    start = max(0, int(round(viewport.start_seconds * ECG_SAMPLE_RATE_HZ)))
    end = min(len(data.samples), int(round((viewport.start_seconds + viewport.duration_seconds) * ECG_SAMPLE_RATE_HZ)))
    return slice(start, max(start, end))


def visible_values(
    data: HistoryEcgData,
    *,
    mode: str,
    viewport: ComparisonViewport,
    y_axis_mode: str,
) -> tuple[list[float], list[float], slice]:
    sample_slice = visible_sample_slice(data, viewport)
    samples = data.samples[sample_slice]
    values = data.clean_values[sample_slice] if mode == "clean" else data.raw_values[sample_slice]
    x_values = [index / float(ECG_SAMPLE_RATE_HZ) for index in range(sample_slice.start or 0, sample_slice.stop or 0)]
    y_values = [float(value) for value in values]
    if y_axis_mode == "normalized" and y_values:
        minimum = min(y_values)
        maximum = max(y_values)
        span = maximum - minimum
        y_values = [0.0 for _ in y_values] if span == 0.0 else [((value - minimum) / span) * 2.0 - 1.0 for value in y_values]
    if len(samples) != len(x_values):
        x_values = x_values[: len(samples)]
        y_values = y_values[: len(samples)]
    return x_values, y_values, sample_slice


def visible_r_peak_indices(data: HistoryEcgData, sample_slice: slice) -> tuple[int, ...]:
    start = sample_slice.start or 0
    stop = sample_slice.stop or 0
    return tuple(index for index in data.r_peak_indices if start <= index < stop)


def visible_markers(track: ComparisonTrack, sample_slice: slice) -> tuple[SessionMarker, ...]:
    if not track.data.samples:
        return ()
    start = sample_slice.start or 0
    stop = sample_slice.stop or 0
    start_sample_index = track.data.samples[0].sample_index + start
    end_sample_index = track.data.samples[0].sample_index + stop
    return tuple(marker for marker in track.markers if start_sample_index <= marker.sample_index < end_sample_index)


def y_range_for(values: list[float]) -> tuple[float, float] | None:
    if not values:
        return None
    minimum = min(values)
    maximum = max(values)
    if minimum == maximum:
        return minimum - 1.0, maximum + 1.0
    margin = (maximum - minimum) * 0.1
    return minimum - margin, maximum + margin


def safe_export_component(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value.strip())
    return cleaned.strip(" ._") or "ECG"


def default_compare_csv_name(record_a: SessionRecord, record_b: SessionRecord, exported_at: datetime | None = None) -> str:
    exported_at = exported_at or datetime.now()
    name_a = safe_export_component(record_a.metadata.display_name)
    name_b = safe_export_component(record_b.metadata.display_name)
    return f"ECG对比_{name_a}_{name_b}_{exported_at.strftime('%Y%m%d_%H%M%S')}.csv"


def write_compare_csv(
    path: Path,
    *,
    track_a: ComparisonTrack,
    track_b: ComparisonTrack,
    viewport: ComparisonViewport,
    export_full: bool,
    alignment_offsets_seconds: dict[str, float] | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    exported_at = datetime.now().astimezone().isoformat(timespec="seconds")
    active_viewport_a = ComparisonViewport(0.0, None) if export_full else viewport
    active_viewport_b = ComparisonViewport(0.0, None) if export_full else viewport
    analysis_info = get_ecg_analysis_info()
    alignment_offsets_seconds = alignment_offsets_seconds or {}
    with path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerows(
            csv_rows_for_track(
                track_a,
                viewport=active_viewport_a,
                exported_at=exported_at,
                analysis_info=analysis_info,
                alignment_offset_seconds=alignment_offsets_seconds.get(track_a.label, 0.0),
            )
        )
        writer.writerows(
            csv_rows_for_track(
                track_b,
                viewport=active_viewport_b,
                exported_at=exported_at,
                analysis_info=analysis_info,
                alignment_offset_seconds=alignment_offsets_seconds.get(track_b.label, 0.0),
            )
        )
    return path


def csv_rows_for_track(
    track: ComparisonTrack,
    *,
    viewport: ComparisonViewport,
    exported_at: str,
    analysis_info: object | None = None,
    alignment_offset_seconds: float = 0.0,
) -> list[dict[str, object]]:
    analysis_info = analysis_info or get_ecg_analysis_info()
    sample_slice = visible_sample_slice(track.data, viewport)
    marker_map = _markers_by_sample_index(track.markers)
    rpeaks = set(track.data.r_peak_indices)
    rr_by_index = _rr_by_rpeak_index(track.data.r_peak_indices)
    rows: list[dict[str, object]] = []
    if not track.data.samples:
        return rows
    first_sample_index = track.data.samples[0].sample_index
    metadata = track.record.metadata
    for index in range(sample_slice.start or 0, sample_slice.stop or 0):
        sample = track.data.samples[index]
        sample_index = sample.sample_index
        time_from_start = (sample_index - first_sample_index) / float(ECG_SAMPLE_RATE_HZ)
        rr_ms = rr_by_index.get(index)
        marker_type, marker_label = marker_map.get(sample_index, ("", ""))
        is_rpeak = index in rpeaks
        rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "exported_at": exported_at,
                "track": track.label,
                "session_id": metadata.session_id,
                "session_name": metadata.display_name,
                "placement_id": metadata.placement_id,
                "placement_name": metadata.placement_name,
                "wire_map": metadata.wire_map,
                "sample_rate_hz": ECG_SAMPLE_RATE_HZ,
                "sample_index": sample_index,
                "time_from_session_start_s": f"{time_from_start:.6f}",
                "time_from_alignment_point_s": f"{time_from_start - alignment_offset_seconds:.6f}",
                "raw_adc": sample.raw_adc,
                "clean_ecg": _format_float(track.data.clean_values[index] if index < len(track.data.clean_values) else None),
                "rpeak_detected": 1 if is_rpeak else 0,
                "rpeak_accepted": 1 if is_rpeak else 0,
                "rr_ms": _format_float(rr_ms),
                "instant_hr_bpm": _format_float(60_000.0 / rr_ms if rr_ms and rr_ms > 0 else None),
                "display_hr_bpm": "",
                "sqi": "",
                "lead_off": 1 if sample.flags & (FLAG_LO_MINUS | FLAG_LO_PLUS) else 0,
                "marker_type": marker_type,
                "marker_label": marker_label,
                "analysis_library": analysis_info.library_name,
                "analysis_library_version": analysis_info.library_version,
                "clean_method": analysis_info.clean_method,
                "peak_method": analysis_info.peak_method,
                "quality_method": analysis_info.quality_method,
            }
        )
    return rows


def _markers_by_sample_index(markers: tuple[SessionMarker, ...]) -> dict[int, tuple[str, str]]:
    grouped: dict[int, list[SessionMarker]] = {}
    for marker in markers:
        grouped.setdefault(marker.sample_index, []).append(marker)
    return {
        sample_index: (
            ";".join(marker.type for marker in sample_markers),
            ";".join(marker.label for marker in sample_markers),
        )
        for sample_index, sample_markers in grouped.items()
    }


def _rr_by_rpeak_index(r_peak_indices: tuple[int, ...]) -> dict[int, float]:
    values: dict[int, float] = {}
    for previous, current in zip(r_peak_indices, r_peak_indices[1:]):
        values[current] = (current - previous) * 1000.0 / float(ECG_SAMPLE_RATE_HZ)
    return values


def _format_float(value: float | int | None) -> str:
    if value is None:
        return ""
    return f"{float(value):.6f}"


def comparison_summary(
    *,
    record_a: SessionRecord,
    data_a: HistoryEcgData,
    markers_a: tuple[SessionMarker, ...],
    record_b: SessionRecord,
    data_b: HistoryEcgData,
    markers_b: tuple[SessionMarker, ...],
    mode: str,
    y_axis_mode: str,
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": mode,
        "y_axis_mode": y_axis_mode,
        "track_a": _track_summary(record_a, data_a, markers_a),
        "track_b": _track_summary(record_b, data_b, markers_b),
    }


def write_json_export(path: Path, data: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _track_summary(
    record: SessionRecord,
    data: HistoryEcgData,
    markers: tuple[SessionMarker, ...],
) -> dict[str, object]:
    metadata = record.metadata
    sample_count = len(data.samples)
    return {
        "session_id": metadata.session_id,
        "display_name": metadata.display_name,
        "placement_id": metadata.placement_id,
        "placement_name": metadata.placement_name,
        "wire_map": metadata.wire_map,
        "sample_count": sample_count,
        "duration_s": sample_count / float(ECG_SAMPLE_RATE_HZ) if sample_count else 0.0,
        "analysis_message": data.message,
        "markers": [
            {
                "id": marker.id,
                "label": marker.label,
                "type": marker.type,
                "sample_index": marker.sample_index,
                "seconds_from_session_start": (
                    (marker.sample_index - data.samples[0].sample_index) / float(ECG_SAMPLE_RATE_HZ)
                    if data.samples
                    else None
                ),
            }
            for marker in markers
        ],
    }
