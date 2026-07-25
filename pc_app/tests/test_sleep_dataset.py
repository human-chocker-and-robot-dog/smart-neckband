from __future__ import annotations

from datetime import datetime
from io import BytesIO
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np

from smart_neckband.protocol import (
    FLAG_HISTORICAL_DATA,
    EcgPayload,
    PacketParser,
)
from smart_neckband.sleep_dataset import (
    convert_slp03_to_session,
    download_slp03,
    sha256_file,
)


class FakeResponse(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


def test_download_verifies_checksum_and_reuses_valid_file(monkeypatch, tmp_path) -> None:
    import smart_neckband.sleep_dataset as module

    payload = b"public sleep data"
    source = tmp_path / "source.bin"
    source.write_bytes(payload)
    expected = sha256_file(source)
    monkeypatch.setattr(module, "SLP03_FILES", {"fixture.bin": expected})
    calls: list[str] = []

    def fake_urlopen(url: str, timeout: int):
        calls.append(url)
        assert timeout == 120
        return FakeResponse(payload)

    monkeypatch.setattr(module, "urlopen", fake_urlopen)
    target = tmp_path / "download"

    first = download_slp03(target)
    second = download_slp03(target)

    assert first == {"fixture.bin": expected}
    assert second == first
    assert calls == [f"{module.SLPDB_BASE_URL}/fixture.bin"]


def test_converter_builds_native_v0_session_with_reference_stages(
    monkeypatch,
    tmp_path,
) -> None:
    import smart_neckband.sleep_dataset as module

    dataset = tmp_path / "dataset"
    dataset.mkdir()
    checksums = {}
    for name in ("slp03.hea", "slp03.dat", "slp03.st", "slp03.ecg"):
        path = dataset / name
        path.write_bytes(name.encode("ascii"))
        checksums[name] = sha256_file(path)
    monkeypatch.setattr(module, "SLP03_FILES", checksums)

    source_values = np.sin(np.linspace(0, 30, 500)).reshape(-1, 1)
    header = SimpleNamespace(
        sig_name=["ECG", "EEG"],
        base_datetime=datetime(1989, 9, 2, 23, 12, 0),
        base_date=None,
        base_time=None,
    )
    record = SimpleNamespace(p_signal=source_values, fs=250)
    annotation = SimpleNamespace(
        sample=np.asarray([0, 250 * 30]),
        aux_note=["W", "2"],
    )
    fake_wfdb = SimpleNamespace(
        rdheader=lambda _path: header,
        rdrecord=lambda _path, channels: record,
        rdann=lambda _path, _extension: annotation,
    )
    fake_signal = SimpleNamespace(
        resample_poly=lambda values, up, down: np.repeat(values, up // down)
    )
    monkeypatch.setitem(sys.modules, "wfdb", fake_wfdb)
    monkeypatch.setitem(sys.modules, "scipy.signal", fake_signal)

    session = convert_slp03_to_session(dataset, tmp_path / "sessions")

    metadata = json.loads((session / "session.json").read_text(encoding="utf-8"))
    reference = json.loads(
        (session / "sleep_reference.json").read_text(encoding="utf-8")
    )
    source = json.loads((session / "source.json").read_text(encoding="utf-8"))
    assert metadata["sample_count"] == 1000
    assert metadata["ecg_sample_rate_hz"] == 500
    assert reference["stages"] == ["WAKE", "NREM"]
    assert source["doi"] == "10.13026/C23K5S"

    parser = PacketParser()
    packets = parser.feed((session / "raw.bin").read_bytes())
    ecg_packets = [packet for packet in packets if isinstance(packet.payload, EcgPayload)]
    assert sum(len(packet.payload.samples) for packet in ecg_packets) == 1000
    assert all(packet.payload.flags & FLAG_HISTORICAL_DATA for packet in ecg_packets)
