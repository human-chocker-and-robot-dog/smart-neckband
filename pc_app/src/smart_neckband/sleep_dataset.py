from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import shutil
import sys
from typing import Callable
from urllib.request import urlopen

import numpy as np

from .protocol import (
    ECG_SAMPLE_COUNT,
    ECG_SAMPLE_RATE_HZ,
    FLAG_ADC_CLIPPING,
    FLAG_HISTORICAL_DATA,
    FLAG_SAMPLE_MISSED,
    encode_ecg_packet,
)
from .sessions import SCHEMA_VERSION, default_analysis_summary
from .sleep_ecg import SLEEP_EPOCH_SECONDS, collapse_reference_stage


SLPDB_BASE_URL = "https://physionet.org/files/slpdb/1.0.0"
SLP03_FILES = {
    "slp03.hea": "9e396d763cf3fd2412f5bcbc234f6b18957df65c4a7734e8c7a587ef97a2ab24",
    "slp03.dat": "f6af00371d59af7c9ea2d48eb3ad75f71269b6c10caaa20daecbd75c7fb05d4d",
    "slp03.st": "1914ba40c87bb3030f22a95eec2017140033cb1889cd585ee4f814285ab04a6d",
    "slp03.ecg": "f13b87ecaf124c880171f109be79eca2f9bf7917c55738c391fe569448d21cf3",
}
SLP03_LICENSE = "Open Data Commons Attribution License v1.0"
SLP03_DOI = "10.13026/C23K5S"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def download_slp03(
    target_dir: Path,
    *,
    progress: Callable[[str], None] | None = None,
) -> dict[str, str]:
    target_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, str] = {}
    for filename, expected in SLP03_FILES.items():
        target = target_dir / filename
        if target.is_file() and sha256_file(target) == expected:
            results[filename] = expected
            _notify(progress, f"verified {filename}")
            continue
        temporary = target.with_suffix(target.suffix + ".part")
        _notify(progress, f"downloading {filename}")
        try:
            with urlopen(f"{SLPDB_BASE_URL}/{filename}", timeout=120) as response:
                with temporary.open("wb") as output:
                    shutil.copyfileobj(response, output, length=1024 * 1024)
            actual = sha256_file(temporary)
            if actual != expected:
                raise RuntimeError(
                    f"SHA-256 mismatch for {filename}: expected {expected}, got {actual}"
                )
            temporary.replace(target)
            results[filename] = actual
        finally:
            if temporary.exists():
                temporary.unlink()
    return results


def convert_slp03_to_session(
    dataset_dir: Path,
    sessions_dir: Path,
    *,
    progress: Callable[[str], None] | None = None,
) -> Path:
    import wfdb
    from scipy.signal import resample_poly

    for filename, expected in SLP03_FILES.items():
        path = dataset_dir / filename
        if not path.is_file():
            raise FileNotFoundError(path)
        actual = sha256_file(path)
        if actual != expected:
            raise RuntimeError(f"Checksum mismatch for {filename}: {actual}")

    base = dataset_dir / "slp03"
    header = wfdb.rdheader(str(base))
    ecg_index = list(header.sig_name).index("ECG")
    record = wfdb.rdrecord(str(base), channels=[ecg_index])
    source = np.asarray(record.p_signal[:, 0], dtype=np.float64)
    source_rate = float(record.fs)
    if source_rate <= 0:
        raise RuntimeError("SLPDB ECG sample rate is invalid")
    _notify(progress, f"loaded {len(source)} ECG samples at {source_rate:g} Hz")

    finite = np.isfinite(source)
    missing_source = ~finite
    replacement = float(np.median(source[finite])) if np.any(finite) else 0.0
    filled = source.copy()
    filled[missing_source] = replacement
    up = ECG_SAMPLE_RATE_HZ
    down = int(round(source_rate))
    resampled = resample_poly(filled, up, down)
    missing = np.repeat(missing_source, up // down) if up % down == 0 else np.zeros(len(resampled), dtype=bool)
    if len(missing) != len(resampled):
        missing = np.resize(missing, len(resampled))

    center = float(np.median(resampled))
    lower, upper = np.percentile(resampled, [0.5, 99.5])
    span = max(abs(float(lower) - center), abs(float(upper) - center), 1e-12)
    counts_per_unit = 1791.0 / span
    unbounded = np.rint((resampled - center) * counts_per_unit + 2048.0)
    clipped = (unbounded < 0) | (unbounded > 4095)
    adc = np.clip(unbounded, 0, 4095).astype(np.uint16)
    valid_sample_count = int(len(adc))
    padding = (-valid_sample_count) % ECG_SAMPLE_COUNT
    if padding:
        adc = np.pad(adc, (0, padding), constant_values=2048)
        clipped = np.pad(clipped, (0, padding), constant_values=False)
        missing = np.pad(missing, (0, padding), constant_values=True)

    start = getattr(header, "base_datetime", None)
    if start is None:
        start = datetime.combine(header.base_date, header.base_time)
    duration = valid_sample_count / ECG_SAMPLE_RATE_HZ
    session_id = "physionet_slp03"
    date_dir = sessions_dir / start.strftime("%Y-%m-%d")
    final_dir = date_dir / session_id
    temp_dir = date_dir / f".{session_id}.tmp"
    if final_dir.exists() or temp_dir.exists():
        raise FileExistsError(
            f"Converted session already exists; move it before retrying: {final_dir}"
        )
    temp_dir.mkdir(parents=True)
    _notify(progress, f"encoding {valid_sample_count} samples as V0 packets")
    raw_path = temp_dir / "raw.bin"
    with raw_path.open("wb") as output:
        output_buffer = bytearray()
        for first in range(0, len(adc), ECG_SAMPLE_COUNT):
            packet_slice = slice(first, first + ECG_SAMPLE_COUNT)
            flags = FLAG_HISTORICAL_DATA
            if bool(np.any(clipped[packet_slice])):
                flags |= FLAG_ADC_CLIPPING
            if bool(np.any(missing[packet_slice])):
                flags |= FLAG_SAMPLE_MISSED
            output_buffer.extend(
                encode_ecg_packet(
                    packet_sequence=(first // ECG_SAMPLE_COUNT) & 0xFFFFFFFF,
                    timestamp_us=first * 1_000_000 // ECG_SAMPLE_RATE_HZ,
                    first_sample_index=first & 0xFFFFFFFF,
                    samples=tuple(int(value) for value in adc[packet_slice]),
                    flags=flags,
                )
            )
            if len(output_buffer) >= 1024 * 1024:
                output.write(output_buffer)
                output_buffer.clear()
        if output_buffer:
            output.write(output_buffer)

    reference = _slp03_reference(base, source_rate)
    markers = []
    previous = None
    for index, stage in enumerate(reference):
        if stage == previous:
            continue
        sample_index = index * SLEEP_EPOCH_SECONDS * ECG_SAMPLE_RATE_HZ
        markers.append(
            {
                "id": f"sleep{len(markers) + 1:04d}",
                "label": stage,
                "type": "sleep_stage_reference",
                "sample_index": sample_index,
                "device_timestamp_us": sample_index * 1_000_000 // ECG_SAMPLE_RATE_HZ,
                "created_at_pc": datetime.now().astimezone().isoformat(timespec="seconds"),
            }
        )
        previous = stage

    metadata = {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "display_name": "PhysioNet SLPDB slp03",
        "placement_id": "P5",
        "placement_name": "Public PSG ECG",
        "wire_map": "SLPDB ECG",
        "electrode_type": "PhysioNet PSG",
        "ecg_sample_rate_hz": ECG_SAMPLE_RATE_HZ,
        "power_mode": "public dataset",
        "connection_type": "imported WFDB",
        "port": "",
        "started_at": start.astimezone().isoformat(timespec="seconds") if start.tzinfo else start.isoformat(timespec="seconds"),
        "ended_at": (start + timedelta(seconds=duration)).isoformat(timespec="seconds"),
        "status": "completed",
        "sample_count": valid_sample_count,
        "notes": f"PhysioNet SLPDB slp03; DOI {SLP03_DOI}; {SLP03_LICENSE}",
        "start_sample_index": 0,
        "end_sample_index": valid_sample_count - 1,
        "interrupted_reason": None,
    }
    _write_json(temp_dir / "session.json", metadata)
    _write_json(
        temp_dir / "markers.json",
        {"schema_version": SCHEMA_VERSION, "markers": markers},
    )
    _write_json(temp_dir / "analysis.json", default_analysis_summary().to_dict())
    _write_json(
        temp_dir / "sleep_reference.json",
        {
            "schema_version": 1,
            "epoch_duration_s": SLEEP_EPOCH_SECONDS,
            "stages": list(reference),
            "source": "slp03.st",
        },
    )
    _write_json(
        temp_dir / "source.json",
        {
            "schema_version": 1,
            "dataset": "MIT-BIH Polysomnographic Database",
            "record_id": "slp03",
            "url": f"{SLPDB_BASE_URL}/",
            "doi": SLP03_DOI,
            "license": SLP03_LICENSE,
            "files": SLP03_FILES,
            "selected_lead": "ECG",
            "source_sample_rate_hz": source_rate,
            "target_sample_rate_hz": ECG_SAMPLE_RATE_HZ,
            "resampling": {"method": "scipy.signal.resample_poly", "up": up, "down": down},
            "adc_mapping": {
                "center_source_units": center,
                "counts_per_source_unit": counts_per_unit,
                "target_midpoint_counts": 2048,
                "robust_percentiles": [0.5, 99.5],
                "clipped_sample_count": int(np.count_nonzero(clipped[:valid_sample_count])),
                "missing_sample_count": int(np.count_nonzero(missing[:valid_sample_count])),
                "tail_padding_sample_count": padding,
            },
        },
    )
    temp_dir.rename(final_dir)
    _notify(progress, f"converted session: {final_dir}")
    return final_dir


def _slp03_reference(base: Path, sample_rate_hz: float) -> tuple[str, ...]:
    import wfdb

    annotation = wfdb.rdann(str(base), "st")
    stages: dict[int, str] = {}
    for sample, note in zip(annotation.sample, annotation.aux_note):
        text = str(note).strip()
        if text:
            stages[int(sample // (sample_rate_hz * SLEEP_EPOCH_SECONDS))] = (
                collapse_reference_stage(text[0])
            )
    if not stages:
        return ()
    return tuple(stages.get(index, "UNDEFINED") for index in range(max(stages) + 1))


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _notify(callback: Callable[[str], None] | None, message: str) -> None:
    if callback is not None:
        callback(message)


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare the open SLPDB slp03 SleepECG fixture")
    parser.add_argument("action", choices=("download", "convert", "prepare"), nargs="?", default="prepare")
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=project_root() / "data" / "sleep_datasets" / "slpdb" / "1.0.0",
    )
    parser.add_argument(
        "--sessions-dir",
        type=Path,
        default=project_root() / "data" / "sessions",
    )
    args = parser.parse_args(argv)
    if args.action in {"download", "prepare"}:
        download_slp03(args.dataset_dir, progress=print)
    if args.action in {"convert", "prepare"}:
        convert_slp03_to_session(args.dataset_dir, args.sessions_dir, progress=print)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
