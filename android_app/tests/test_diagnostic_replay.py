import json
from pathlib import Path
import pytest
from smart_neckband.diagnostics import DiagnosticCapture, load_capture, replay_capture
from smart_neckband.protocol import encode_ecg_packet


def test_saved_transport_replays_same_complete_ecg_through_both_algorithms(tmp_path):
    root = Path(__file__).resolve().parents[2]
    fixture = json.loads((root / "android_app/app/src/androidTest/assets/synthetic-ecg.json").read_text())
    capture = DiagnosticCapture()
    for packet in range(250):
        data = encode_ecg_packet(packet_sequence=packet, timestamp_us=packet * 44_000,
            first_sample_index=packet * 20, samples=tuple(fixture["samples"][packet * 20:packet * 20 + 20]), flags=16)
        for offset in range(0, len(data), 20):
            capture.transport(data[offset:offset + 20])
    result = replay_capture(load_capture(capture.export(tmp_path / "synthetic.jsonl")), root / "android_app/app/src/main/python")
    assert result["raw_count"] == result["analysis_count"] == 5000
    assert result["pc_clean_count"] == result["phone_clean_count"] == 5000
    assert result["clean_max_abs_error"] == 0
    assert result["phone_bpm"] == pytest.approx(fixture["bpm"])
    assert result["phone_sqi"] == pytest.approx(fixture["quality"])
