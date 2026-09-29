from smart_neckband.diagnostics import DiagnosticCapture, load_capture, decode_capture, inspect_capture, compare_captures
from smart_neckband.protocol import encode_ecg_packet


def wire(first=0, timestamp=0, flags=16):
    return encode_ecg_packet(packet_sequence=10, timestamp_us=timestamp,
        first_sample_index=first, samples=tuple(range(2048, 2068)), flags=flags)


def make_capture(tmp_path, name="capture.jsonl", first=0, timestamp=0):
    capture = DiagnosticCapture()
    capture.begin_connection()
    packet = wire(first, timestamp)
    for index in range(0, len(packet), 7):
        capture.transport(packet[index:index + 7])
    return load_capture(capture.export(tmp_path / name))


def test_capture_keeps_fragments_and_detects_native_decoder_difference(tmp_path):
    rows = make_capture(tmp_path)
    batches, _ = decode_capture(rows)
    assert batches[0]["samples"] == list(range(2048, 2068))
    rows.append(dict(type="frame", connection_id=batches[0]["connection_id"], data=batches[0]))
    assert inspect_capture(rows)["native_decode_mismatch_count"] == 0
    rows[-1]["data"]["samples"][5] = 0
    assert inspect_capture(rows)["native_decode_mismatch_count"] == 1


def test_separate_acquisitions_are_not_reported_as_equal(tmp_path):
    a = make_capture(tmp_path)
    b = make_capture(tmp_path, "b.jsonl", first=500, timestamp=1_000_000)
    result = compare_captures(a, b)
    assert result["status"] == "no_overlap"
    assert result["common_samples"] == 0
    assert compare_captures(a, a)["common_samples"] == 20


def test_crc_corruption_is_reported_without_inventing_samples(tmp_path):
    capture = DiagnosticCapture()
    corrupted = bytearray(wire())
    corrupted[30] ^= 1
    capture.transport(corrupted)
    rows = load_capture(capture.export(tmp_path / "bad.jsonl"))
    result = inspect_capture(rows)
    assert result["transport"]["crc_errors"] == 1
    assert result["decoded_ecg_samples"] == 0


def test_memory_and_idle_expiry_are_bounded(tmp_path):
    now = [0.0]
    capture = DiagnosticCapture(clock=lambda: now[0], max_bytes=800)
    for _ in range(30):
        capture.transport(b"x" * 20)
    rows = load_capture(capture.export(tmp_path / "limited.jsonl"))
    assert rows[0]["evicted_records"] > 0
    assert len(rows) < 10
    now[0] = 61
    assert len(load_capture(capture.export(tmp_path / "expired.jsonl"))) == 1


def test_connection_change_cannot_join_partial_frames(tmp_path):
    capture = DiagnosticCapture()
    packet = wire()
    capture.transport(packet[:30])
    capture.begin_connection()
    capture.transport(packet[30:])
    assert decode_capture(load_capture(capture.export(tmp_path / "reset.jsonl")))[0] == []
