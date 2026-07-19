from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Iterable

from .buffers import EcgSample
from .protocol import ECG_SAMPLE_RATE_HZ, EcgPayload, PacketParser
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
    with raw_path.open("rb") as raw_file:
        while chunk := raw_file.read(64 * 1024):
            for packet in parser.feed(chunk):
                payload = packet.payload
                if not isinstance(payload, EcgPayload):
                    continue
                sample_period_us = int(1_000_000 / payload.sample_rate_hz)
                for offset, raw_adc in enumerate(payload.samples):
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
