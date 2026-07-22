from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
import re
from threading import Lock
from typing import Any

from .analysis import get_ecg_analysis_info
from .protocol import ECG_SAMPLE_RATE_HZ
from .recorder import RawBinaryRecorder

SCHEMA_VERSION = 1


class RecordingState(str, Enum):
    IDLE = "IDLE"
    COUNTDOWN = "COUNTDOWN"
    WAITING_FIRST_VALID_SAMPLE = "WAITING_FIRST_VALID_SAMPLE"
    RECORDING = "RECORDING"
    STOPPING = "STOPPING"
    SAVING = "SAVING"
    SAVED = "SAVED"
    INTERRUPTED = "INTERRUPTED"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class PlacementPreset:
    placement_id: str
    placement_name: str


PLACEMENT_PRESETS: tuple[PlacementPreset, ...] = (
    PlacementPreset("P0", "胸前参考"),
    PlacementPreset("P1", "左右锁骨内侧"),
    PlacementPreset("P2", "左右锁骨上窝"),
    PlacementPreset("P3", "前颈下缘与锁骨组合"),
    PlacementPreset("P4", "后颈对照"),
    PlacementPreset("P5", "自定义"),
)

WIRE_MAPS: tuple[tuple[str, str], ...] = (
    ("A", "A：红色 R 为右侧测量，绿色 R 为参考"),
    ("B", "B：绿色 R 为右侧测量，红色 R 为参考"),
    ("未知", "未知"),
)


@dataclass(frozen=True, slots=True)
class SessionMetadata:
    schema_version: int
    session_id: str
    display_name: str
    placement_id: str
    placement_name: str
    wire_map: str
    electrode_type: str
    ecg_sample_rate_hz: int
    power_mode: str
    connection_type: str
    port: str
    started_at: str | None
    ended_at: str | None
    status: str
    sample_count: int
    notes: str
    start_sample_index: int | None = None
    end_sample_index: int | None = None
    interrupted_reason: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionMetadata:
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"不支持的 session schema_version: {data.get('schema_version')}")
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SessionMarker:
    id: str
    label: str
    type: str
    sample_index: int
    device_timestamp_us: int
    created_at_pc: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SessionAnalysisSummary:
    schema_version: int
    analysis_library: str
    analysis_library_version: str
    numpy_version: str
    sampling_rate_hz: int
    clean_method: str
    peak_method: str
    quality_method: str
    median_hr_bpm: float | None
    valid_rr_ratio: float | None
    mean_sqi: float | None
    clipping_ratio: float | None
    analysis_completed: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def safe_session_component(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", value.strip())
    return cleaned.strip("_") or "session"


def make_session_id(started_at: datetime, placement_id: str, wire_map: str) -> str:
    return f"{started_at.strftime('%Y%m%d_%H%M%S')}_{safe_session_component(placement_id)}_{safe_session_component(wire_map)}"


def sample_index_to_seconds(sample_index: int, sample_rate_hz: int = ECG_SAMPLE_RATE_HZ) -> float:
    return sample_index / float(sample_rate_hz)


def default_analysis_summary() -> SessionAnalysisSummary:
    info = get_ecg_analysis_info()
    return SessionAnalysisSummary(
        schema_version=SCHEMA_VERSION,
        analysis_library=info.library_name,
        analysis_library_version=info.library_version,
        numpy_version=info.numpy_version,
        sampling_rate_hz=info.sampling_rate_hz,
        clean_method=info.clean_method,
        peak_method=info.peak_method,
        quality_method=info.quality_method,
        median_hr_bpm=None,
        valid_rr_ratio=None,
        mean_sqi=None,
        clipping_ratio=None,
        analysis_completed=False,
    )


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_session_metadata(path: Path) -> SessionMetadata:
    return SessionMetadata.from_dict(json.loads(path.read_text(encoding="utf-8")))


class ExperimentSessionRecorder:
    def __init__(
        self,
        *,
        base_dir: Path,
        display_name: str,
        placement: PlacementPreset,
        wire_map: str,
        electrode_type: str,
        notes: str,
        port: str,
        connection_type: str = "Bluetooth Classic SPP",
        created_at: datetime | None = None,
    ) -> None:
        created_at = created_at or datetime.now(timezone.utc).astimezone()
        self.session_id = make_session_id(created_at, placement.placement_id, wire_map)
        self.date_dir = base_dir / created_at.strftime("%Y-%m-%d")
        self.temp_dir = self.date_dir / f".{self.session_id}.tmp"
        self.final_dir = self.date_dir / self.session_id
        self.temp_dir.mkdir(parents=True, exist_ok=False)
        self.raw_path = self.temp_dir / "raw.bin"
        self.markers: list[SessionMarker] = []
        self._lock = Lock()
        self._raw = RawBinaryRecorder(self.raw_path)
        self._started_at: str | None = None
        self._start_sample_index: int | None = None
        self._latest_sample_index: int | None = None
        self._state = RecordingState.WAITING_FIRST_VALID_SAMPLE
        self._metadata_seed = {
            "display_name": display_name.strip() or self.session_id,
            "placement_id": placement.placement_id,
            "placement_name": placement.placement_name,
            "wire_map": wire_map,
            "electrode_type": electrode_type.strip(),
            "ecg_sample_rate_hz": ECG_SAMPLE_RATE_HZ,
            "power_mode": "独立电池",
            "connection_type": connection_type,
            "port": port,
            "notes": notes.strip(),
        }

    @property
    def state(self) -> RecordingState:
        return self._state

    @property
    def sample_count(self) -> int:
        if self._start_sample_index is None or self._latest_sample_index is None:
            return 0
        return max(0, self._latest_sample_index - self._start_sample_index + 1)

    def start_at_sample(self, sample_index: int) -> None:
        with self._lock:
            if self._state is not RecordingState.WAITING_FIRST_VALID_SAMPLE:
                return
            self._start_sample_index = sample_index
            self._latest_sample_index = sample_index
            self._started_at = now_iso()
            self._state = RecordingState.RECORDING

    def update_latest_sample(self, sample_index: int) -> None:
        with self._lock:
            if self._state is RecordingState.RECORDING:
                self._latest_sample_index = max(sample_index, self._latest_sample_index or sample_index)

    def add_marker(self, *, label: str, marker_type: str, sample_index: int, device_timestamp_us: int) -> SessionMarker:
        with self._lock:
            marker = SessionMarker(
                id=f"m{len(self.markers) + 1:04d}",
                label=label.strip() or marker_type,
                type=marker_type,
                sample_index=sample_index,
                device_timestamp_us=device_timestamp_us,
                created_at_pc=now_iso(),
            )
            self.markers.append(marker)
            return marker

    def write_raw(self, data: bytes) -> None:
        if self._state is not RecordingState.RECORDING:
            return
        self._raw.write(data)

    def finish(self, *, status: str, interrupted_reason: str | None = None) -> Path:
        with self._lock:
            self._state = RecordingState.SAVING
            ended_at = now_iso()
            metadata = SessionMetadata(
                schema_version=SCHEMA_VERSION,
                session_id=self.session_id,
                started_at=self._started_at,
                ended_at=ended_at,
                status=status,
                sample_count=self.sample_count,
                start_sample_index=self._start_sample_index,
                end_sample_index=self._latest_sample_index,
                interrupted_reason=interrupted_reason,
                **self._metadata_seed,
            )
            self._raw.close()
            write_json(self.temp_dir / "session.json", metadata.to_dict())
            write_json(
                self.temp_dir / "markers.json",
                {
                    "schema_version": SCHEMA_VERSION,
                    "markers": [marker.to_dict() for marker in self.markers],
                },
            )
            write_json(self.temp_dir / "analysis.json", default_analysis_summary().to_dict())
            self.temp_dir.rename(self.final_dir)
            self._state = RecordingState.SAVED if status == "completed" else RecordingState.INTERRUPTED
            return self.final_dir
