from datetime import datetime

from smart_neckband.history import (
    analyze_history_ecg,
    downsample_xy,
    list_session_records,
    load_session_ecg_samples,
    load_session_markers,
)
from smart_neckband.protocol import ECG_SAMPLE_COUNT, encode_ecg_packet
from smart_neckband.sessions import PLACEMENT_PRESETS, ExperimentSessionRecorder


def _write_session(tmp_path):
    recorder = ExperimentSessionRecorder(
        base_dir=tmp_path,
        display_name="历史测试",
        placement=PLACEMENT_PRESETS[1],
        wire_map="A",
        electrode_type="AgAgCl",
        notes="",
        port="COM19",
        created_at=datetime(2026, 7, 19, 12, 30, 0),
    )
    recorder.start_at_sample(0)
    recorder.add_marker(label="吞咽", marker_type="swallow", sample_index=2, device_timestamp_us=4_000)
    recorder.write_raw(
        encode_ecg_packet(
            packet_sequence=1,
            timestamp_us=0,
            first_sample_index=0,
            samples=tuple(range(ECG_SAMPLE_COUNT)),
        )
    )
    return recorder.finish(status="completed")


def test_list_session_records_and_markers(tmp_path) -> None:
    session_dir = _write_session(tmp_path)

    records = list_session_records(tmp_path)
    markers = load_session_markers(session_dir)

    assert len(records) == 1
    assert records[0].metadata.display_name == "历史测试"
    assert markers[0].label == "吞咽"
    assert markers[0].sample_index == 2


def test_load_session_ecg_samples_from_raw_bin(tmp_path) -> None:
    session_dir = _write_session(tmp_path)

    samples = load_session_ecg_samples(session_dir)

    assert len(samples) == ECG_SAMPLE_COUNT
    assert samples[0].sample_index == 0
    assert samples[1].timestamp_us == 2_000
    assert samples[-1].raw_adc == ECG_SAMPLE_COUNT - 1


def test_analyze_history_ecg_short_window_returns_raw() -> None:
    data = analyze_history_ecg(())

    assert data.raw_values == ()
    assert data.clean_values == ()
    assert data.message == "少于 1 秒 ECG，未运行 NeuroKit2"


def test_downsample_xy_limits_points() -> None:
    x_values, y_values = downsample_xy(range(100), range(100), max_points=10)

    assert len(x_values) == 10
    assert x_values[1] == 10
    assert y_values[-1] == 90
