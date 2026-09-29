"""Local bounded captures and offline cross-host ECG evidence. No device I/O."""
from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timezone
from importlib import metadata
import json
from pathlib import Path
from threading import Lock
import time
from uuid import uuid4

from .protocol import PacketParser, EcgPayload, ImuPayload
from .unified_stream import UnifiedStreamDemux, UnifiedFrameKind


class DiagnosticCapture:
    def __init__(self, *, clock=time.monotonic, max_bytes=16 * 1024 * 1024, seconds=60):
        self.clock, self.max_bytes, self.seconds = clock, max_bytes, seconds
        self._entries = deque()
        self._size = 0
        self._evicted = 0
        self._lock = Lock()
        self._connection = str(uuid4())

    def begin_connection(self):
        with self._lock:
            self._connection = str(uuid4())
        self.add("state", {"state": "connecting"})

    def _trim(self, now):
        while self._entries and (now - self._entries[0][0] > self.seconds or self._size > self.max_bytes):
            _, line = self._entries.popleft()
            self._size -= len(line.encode("utf-8"))
            self._evicted += 1

    def add(self, kind, data):
        with self._lock:
            now = self.clock()
            line = json.dumps(dict(type=kind, elapsed_ms=now * 1000,
                connection_id=self._connection, data=data), ensure_ascii=False, allow_nan=False)
            self._entries.append((now, line))
            self._size += len(line.encode("utf-8"))
            self._trim(now)

    def transport(self, chunk):
        self.add("transport", {"hex": bytes(chunk).hex()})

    def export(self, path, details=None):
        with self._lock:
            self._trim(self.clock())
            lines = [line for _, line in self._entries]
            evicted = self._evicted
        header = dict(type="meta", schema=1, platform="pc", source="live_transport",
            captured_at=datetime.now(timezone.utc).isoformat(), retention_ms=self.seconds * 1000,
            evicted_records=evicted, details=details or {})
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text("\n".join([json.dumps(header, ensure_ascii=False), *lines]) + "\n", encoding="utf-8")
        temporary.replace(path)
        return path


def load_capture(path):
    path = Path(path)
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("Capture exceeds 64 MiB")
    with path.open(encoding="utf-8-sig") as stream:
        rows = [json.loads(line) for line in stream if line.strip()]
    if not rows or rows[0].get("type") != "meta" or rows[0].get("schema") != 1:
        raise ValueError("Expected diagnostic JSONL schema 1")
    return rows


def decode_capture(rows):
    """Decode exact saved notification fragments through the PC unified parser."""
    batches = []
    connection = None
    demux, parser = UnifiedStreamDemux(), PacketParser()
    crc_errors = discarded = 0
    for row in rows:
        if row.get("type") != "transport":
            continue
        if row["connection_id"] != connection:
            crc_errors += demux.stats.crc_errors
            discarded += demux.stats.discarded_bytes
            connection = row["connection_id"]
            demux, parser = UnifiedStreamDemux(), PacketParser()
        for wire in demux.feed(bytes.fromhex(row["data"]["hex"])):
            if wire.kind != UnifiedFrameKind.V0:
                continue
            for packet in parser.feed(wire.data):
                payload = packet.payload
                if not isinstance(payload, (EcgPayload, ImuPayload)):
                    continue
                batches.append(dict(kind="ecg" if isinstance(payload, EcgPayload) else "imu",
                    first=payload.first_sample_index, timestamp=packet.header.timestamp_us,
                    sequence=packet.header.packet_sequence, rate=payload.sample_rate_hz, flags=payload.flags,
                    samples=list(payload.samples) if isinstance(payload, EcgPayload) else
                        [[p.ax, p.ay, p.az, p.gx, p.gy, p.gz] for p in payload.samples],
                    connection_id=connection, elapsed_ms=row["elapsed_ms"]))
    return batches, dict(crc_errors=crc_errors + demux.stats.crc_errors,
        discarded_bytes=discarded + demux.stats.discarded_bytes)


def frame_key(batch):
    return batch["connection_id"], batch["kind"], batch["sequence"], batch["timestamp"]


def inspect_capture(rows):
    batches, stats = decode_capture(rows)
    decoded = {frame_key(b): b for b in batches}
    mismatches = []
    checked = 0
    missing = 0
    # Android's native decoder is compared with the PC decoder on the SAME bytes.
    for row in rows:
        if row.get("type") != "frame" or row["data"].get("kind") not in ("ecg", "imu"):
            continue
        logged = dict(row["data"], connection_id=row["connection_id"])
        expected = decoded.get(frame_key(logged))
        if expected is None:
            missing += 1  # Ring may start midway through a packet; do not call this corruption.
            continue
        checked += 1
        if any(logged.get(key) != expected.get(key) for key in ("first", "rate", "flags", "samples")):
            mismatches.append({k: logged[k] for k in ("kind", "sequence", "timestamp")})
    ecg = [b for b in batches if b["kind"] == "ecg"]
    flags = {}
    gaps = 0
    previous = None
    for batch in ecg:
        flags[str(batch["flags"])] = flags.get(str(batch["flags"]), 0) + 1
        if previous and previous["connection_id"] == batch["connection_id"]:
            gaps += int(batch["first"] != (previous["first"] + len(previous["samples"])) % 2**32)
        previous = batch
    return dict(metadata=rows[0], decoded_ecg_packets=len(ecg), decoded_ecg_samples=sum(len(b["samples"]) for b in ecg),
        sample_index_gaps=gaps, flags=flags, transport=stats, native_frames_checked=checked,
        native_decode_mismatches=mismatches[:20], native_decode_mismatch_count=len(mismatches),
        native_frames_without_retained_wire=missing,
        last_analysis=next(({k: v for k, v in r["data"].items() if k not in ("raw", "cleaned")}
            for r in reversed(rows) if r.get("type") == "analysis"), None))


def compare_captures(a, b):
    def samples(rows):
        batches, _ = decode_capture(rows)
        result = {}
        for batch in batches:
            if batch["kind"] != "ecg":
                continue
            for offset, value in enumerate(batch["samples"]):
                key = (batch["first"] + offset, batch["timestamp"] + offset * 2000)
                result[key] = (value, batch["flags"])
        return result
    left, right = samples(a), samples(b)
    common = left.keys() & right.keys()
    differences = [key for key in sorted(common) if left[key] != right[key]]
    return dict(status="candidate_overlap" if common else "no_overlap",
        note="Device index/time overlap is only a candidate; confirm same device boot. Prefer replaying one capture.",
        common_samples=len(common), different_samples=len(differences), only_a=len(left.keys() - right.keys()),
        only_b=len(right.keys() - left.keys()), first_differences=[dict(index=k[0], timestamp_us=k[1], a=left[k], b=right[k]) for k in differences[:20]])


def replay_capture(rows, android_source):
    """Run the phone adapter and PC cleaner on one captured stream, on this host.

    This isolates algorithm/window policy. It does not simulate Android scheduling
    and is explicitly not proof of BLE delivery or native-library parity.
    """
    import importlib.util
    from .analysis import analyze_recent_ecg
    spec = importlib.util.spec_from_file_location("diagnostic_android_engine", Path(android_source) / "collar_engine.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = module.Engine()
    batches, stats = decode_capture(rows)
    if not batches:
        return dict(status="no_samples", transport=stats)
    # Prefer the last actual phone analysis point so its raw plot can be compared.
    recorded = next((r for r in reversed(rows) if r.get("type") == "analysis" and r["data"].get("window_last") is not None), None)
    connection = recorded["connection_id"] if recorded else batches[-1]["connection_id"]
    processed = [r for r in rows if r.get("type") == "processed" and r["connection_id"] == connection
                 and (recorded is None or r["elapsed_ms"] <= recorded["elapsed_ms"])]
    # Android logs the precise drain boundary (notifications may arrive during analysis).
    source = [r["data"] for r in processed] if processed else [b for b in batches if b["connection_id"] == connection]
    if recorded and not processed:
        source = [b for b in source if b["kind"] != "ecg" or b["first"] <= recorded["data"]["window_last"]]
    for batch in source:
        engine.append(json.dumps(dict(batch, received_age_ms=0)))
    before = tuple(engine.ecg)
    actual = json.loads(engine.analyze())
    expected = analyze_recent_ecg(before)
    clean = actual["cleaned"]["values"]
    error = max((abs(x - y) for x, y in zip(clean, expected.cleaned)), default=0.0) if len(clean) == len(expected.cleaned) else None
    saved_raw = recorded["data"].get("raw", {}).get("values", []) if recorded else None
    return dict(status="replayed", execution="host_python_not_android_runtime",
        versions={name: metadata.version(name) for name in ("neurokit2", "numpy", "scipy")},
        source="phone_processed_batches" if processed else "pc_decoded_transport",
        batches=len(source), raw_count=len(actual["raw"]["values"]), analysis_count=len(before),
        pc_clean_count=len(expected.cleaned), phone_clean_count=len(clean), clean_max_abs_error=error,
        pc_bpm=expected.heart_rate_bpm, phone_bpm=actual["bpm"], pc_sqi=expected.signal_quality,
        phone_sqi=actual["quality"], phone_message=actual["message"],
        recorded_phone_raw_equals_replay=(saved_raw == actual["raw"]["values"]) if recorded else None,
        note="HR/HRV may intentionally be suppressed by phone quality/freshness gates. Compare versions and retained window before declaring a defect.")


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("capture", type=Path)
    cli.add_argument("--compare", type=Path)
    cli.add_argument("--android-source", type=Path, help="Path to android_app/app/src/main/python for same-input replay")
    cli.add_argument("--output", type=Path)
    args = cli.parse_args()
    rows = load_capture(args.capture)
    result = {"capture": inspect_capture(rows)}
    if args.compare:
        result["comparison"] = compare_captures(rows, load_capture(args.compare))
    if args.android_source:
        result["replay"] = replay_capture(rows, args.android_source)
    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
