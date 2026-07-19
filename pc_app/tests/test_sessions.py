from datetime import datetime
import json

from smart_neckband.protocol import ECG_SAMPLE_RATE_HZ
from smart_neckband.sessions import (
    PLACEMENT_PRESETS,
    ExperimentSessionRecorder,
    RecordingState,
    read_session_metadata,
    safe_session_component,
    sample_index_to_seconds,
)


def test_safe_session_component_keeps_chinese_and_ascii() -> None:
    assert safe_session_component(" P1 左右锁骨/内侧 ") == "P1_左右锁骨_内侧"
    assert safe_session_component("   ") == "session"


def test_sample_index_to_seconds_uses_ecg_sample_rate() -> None:
    assert sample_index_to_seconds(ECG_SAMPLE_RATE_HZ * 3) == 3.0


def test_session_recorder_writes_completed_session_files(tmp_path) -> None:
    recorder = ExperimentSessionRecorder(
        base_dir=tmp_path,
        display_name="锁骨内侧第一次",
        placement=PLACEMENT_PRESETS[1],
        wire_map="A",
        electrode_type="AgAgCl 湿电极",
        notes="测试备注",
        port="COM19",
        created_at=datetime(2026, 7, 19, 12, 0, 0),
    )

    recorder.write_raw(b"before-start")
    recorder.start_at_sample(100)
    recorder.write_raw(b"packet-1")
    recorder.update_latest_sample(139)
    recorder.write_raw(b"packet-2")
    final_dir = recorder.finish(status="completed")

    assert recorder.state is RecordingState.SAVED
    assert final_dir.name == "20260719_120000_P1_A"
    assert not recorder.temp_dir.exists()
    assert (final_dir / "raw.bin").read_bytes() == b"packet-1packet-2"

    metadata = read_session_metadata(final_dir / "session.json")
    assert metadata.display_name == "锁骨内侧第一次"
    assert metadata.placement_id == "P1"
    assert metadata.placement_name == "左右锁骨内侧"
    assert metadata.wire_map == "A"
    assert metadata.port == "COM19"
    assert metadata.status == "completed"
    assert metadata.sample_count == 40
    assert metadata.start_sample_index == 100
    assert metadata.end_sample_index == 139
    assert metadata.interrupted_reason is None

    markers = json.loads((final_dir / "markers.json").read_text(encoding="utf-8"))
    assert markers == {"schema_version": 1, "markers": []}
    analysis = json.loads((final_dir / "analysis.json").read_text(encoding="utf-8"))
    assert analysis["analysis_completed"] is False
    assert analysis["sampling_rate_hz"] == ECG_SAMPLE_RATE_HZ


def test_session_recorder_saves_interrupted_waiting_session(tmp_path) -> None:
    recorder = ExperimentSessionRecorder(
        base_dir=tmp_path,
        display_name="",
        placement=PLACEMENT_PRESETS[0],
        wire_map="未知",
        electrode_type="",
        notes="",
        port="COM19",
        created_at=datetime(2026, 7, 19, 12, 0, 1),
    )

    final_dir = recorder.finish(status="interrupted", interrupted_reason="用户取消倒计时")

    metadata = read_session_metadata(final_dir / "session.json")
    assert recorder.state is RecordingState.INTERRUPTED
    assert metadata.status == "interrupted"
    assert metadata.sample_count == 0
    assert metadata.started_at is None
    assert metadata.interrupted_reason == "用户取消倒计时"
    assert (final_dir / "raw.bin").read_bytes() == b""
