import csv
from datetime import datetime

from smart_neckband.history import (
    ComparisonTrack,
    ComparisonViewport,
    analyze_history_ecg,
    clamp_viewport,
    comparison_summary,
    csv_rows_for_track,
    default_compare_csv_name,
    downsample_xy,
    list_session_records,
    load_session_ecg_samples,
    load_session_markers,
    session_duration_seconds,
    visible_r_peak_indices,
    visible_markers,
    visible_sample_slice,
    visible_values,
    write_json_export,
    write_compare_csv,
)
from smart_neckband.protocol import ECG_SAMPLE_COUNT, ECG_SAMPLE_RATE_HZ, encode_ecg_packet
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


def _write_long_session(tmp_path):
    recorder = ExperimentSessionRecorder(
        base_dir=tmp_path,
        display_name="长记录",
        placement=PLACEMENT_PRESETS[2],
        wire_map="B",
        electrode_type="AgAgCl",
        notes="",
        port="COM19",
        created_at=datetime(2026, 7, 19, 12, 31, 0),
    )
    recorder.start_at_sample(0)
    recorder.add_marker(label="转头", marker_type="turn", sample_index=ECG_SAMPLE_RATE_HZ, device_timestamp_us=1_000_000)
    sequence = 1
    for first_sample_index in range(0, ECG_SAMPLE_RATE_HZ * 12, ECG_SAMPLE_COUNT):
        recorder.write_raw(
            encode_ecg_packet(
                packet_sequence=sequence,
                timestamp_us=first_sample_index * 2_000,
                first_sample_index=first_sample_index,
                samples=tuple(2000 + ((first_sample_index + offset) % 100) for offset in range(ECG_SAMPLE_COUNT)),
            )
        )
        sequence += 1
    recorder.update_latest_sample(ECG_SAMPLE_RATE_HZ * 12 - 1)
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


def test_comparison_summary_and_export(tmp_path) -> None:
    session_dir = _write_session(tmp_path)
    record = list_session_records(tmp_path)[0]
    data = analyze_history_ecg(load_session_ecg_samples(session_dir))
    markers = load_session_markers(session_dir)

    summary = comparison_summary(
        record_a=record,
        data_a=data,
        markers_a=markers,
        record_b=record,
        data_b=data,
        markers_b=markers,
        mode="raw",
        y_axis_mode="zero_mean",
    )
    export_path = write_json_export(tmp_path / "exports" / "compare.json", summary)

    assert summary["track_a"]["display_name"] == "历史测试"
    assert summary["track_a"]["markers"][0]["seconds_from_session_start"] == 0.004
    assert export_path.exists()


def test_compare_viewport_defaults_to_ten_second_window(tmp_path) -> None:
    session_dir = _write_long_session(tmp_path)
    data = analyze_history_ecg(load_session_ecg_samples(session_dir))
    viewport = clamp_viewport(ComparisonViewport(0.0, 10.0), max_duration_seconds=session_duration_seconds(data))

    sample_slice = visible_sample_slice(data, viewport)

    assert viewport.start_seconds == 0.0
    assert viewport.duration_seconds == 10.0
    assert sample_slice.stop - sample_slice.start == ECG_SAMPLE_RATE_HZ * 10


def test_compare_viewport_clamps_to_record_end(tmp_path) -> None:
    session_dir = _write_long_session(tmp_path)
    data = analyze_history_ecg(load_session_ecg_samples(session_dir))
    viewport = clamp_viewport(ComparisonViewport(99.0, 10.0), max_duration_seconds=session_duration_seconds(data))

    assert viewport.start_seconds == 2.0
    assert viewport.duration_seconds == 10.0


def test_visible_values_normalization_does_not_mutate_clean_data(tmp_path) -> None:
    session_dir = _write_long_session(tmp_path)
    data = analyze_history_ecg(load_session_ecg_samples(session_dir))
    before = data.clean_values

    _x_values, y_values, _sample_slice = visible_values(
        data,
        mode="clean",
        viewport=ComparisonViewport(0.0, 5.0),
        y_axis_mode="normalized",
    )

    assert data.clean_values == before
    assert min(y_values) >= -1.0
    assert max(y_values) <= 1.0


def test_visible_peaks_and_markers_are_track_local(tmp_path) -> None:
    session_dir = _write_long_session(tmp_path)
    record = list_session_records(tmp_path)[0]
    data = analyze_history_ecg(load_session_ecg_samples(session_dir))
    data = type(data)(
        samples=data.samples,
        raw_values=data.raw_values,
        clean_values=data.clean_values,
        r_peak_indices=(10, 600),
        message=data.message,
    )
    markers = load_session_markers(session_dir)
    track = ComparisonTrack("A", record, data, markers)
    sample_slice = visible_sample_slice(data, ComparisonViewport(0.0, 2.0))

    assert visible_r_peak_indices(data, sample_slice) == (10, 600)
    assert [marker.label for marker in visible_markers(track, sample_slice)] == ["转头"]


def test_csv_rows_include_raw_clean_rpeak_and_marker(tmp_path) -> None:
    session_dir = _write_long_session(tmp_path)
    record = list_session_records(tmp_path)[0]
    data = analyze_history_ecg(load_session_ecg_samples(session_dir))
    data = type(data)(
        samples=data.samples,
        raw_values=data.raw_values,
        clean_values=tuple(float(index) / 10.0 for index in range(len(data.samples))),
        r_peak_indices=(ECG_SAMPLE_RATE_HZ,),
        message=data.message,
    )
    markers = load_session_markers(session_dir)
    track = ComparisonTrack("A", record, data, markers)

    rows = csv_rows_for_track(
        track,
        viewport=ComparisonViewport(0.0, 2.0),
        exported_at="2026-07-19T12:00:00+08:00",
        alignment_offset_seconds=1.0,
    )
    marker_row = rows[ECG_SAMPLE_RATE_HZ]

    assert len(rows) == ECG_SAMPLE_RATE_HZ * 2
    assert marker_row["track"] == "A"
    assert marker_row["raw_adc"] == data.samples[ECG_SAMPLE_RATE_HZ].raw_adc
    assert marker_row["clean_ecg"] == "50.000000"
    assert marker_row["rpeak_detected"] == 1
    assert marker_row["marker_type"] == "turn"
    assert marker_row["marker_label"] == "转头"
    assert marker_row["time_from_alignment_point_s"] == "0.000000"


def test_write_compare_csv_uses_bom_and_track_column(tmp_path) -> None:
    session_dir = _write_long_session(tmp_path)
    record = list_session_records(tmp_path)[0]
    data = analyze_history_ecg(load_session_ecg_samples(session_dir))
    markers = load_session_markers(session_dir)
    track_a = ComparisonTrack("A", record, data, markers)
    track_b = ComparisonTrack("B", record, data, markers)
    export_path = tmp_path / default_compare_csv_name(record, record, datetime(2026, 7, 19, 12, 0, 0))

    write_compare_csv(
        export_path,
        track_a=track_a,
        track_b=track_b,
        viewport=ComparisonViewport(0.0, 1.0),
        export_full=False,
    )

    raw_bytes = export_path.read_bytes()
    assert raw_bytes.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(export_path.read_text(encoding="utf-8-sig").splitlines()))
    assert len(rows) == ECG_SAMPLE_RATE_HZ * 2
    assert {row["track"] for row in rows} == {"A", "B"}
    assert rows[0]["raw_adc"] != ""
    assert rows[0]["clean_ecg"] != ""
