from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Any

from .analysis import EcgAnalysisResult, analyze_recent_ecg
from .buffers import EcgSample, StatusSample
from .protocol import ECG_SAMPLE_RATE_HZ, FLAG_LO_MINUS, FLAG_LO_PLUS
from .serial_io import PcDataStores, SerialPacketReader


@dataclass(frozen=True, slots=True)
class UploadBatch:
    message: dict[str, Any]
    last_sample_index: int


def _valid_hr(value: float | None) -> float | None:
    if value is None or not 20.0 <= value <= 240.0:
        return None
    return float(value)


def _valid_sqi(value: float | None) -> float | None:
    if value is None:
        return None
    return min(1.0, max(0.0, float(value)))


def lead_off_from_status(status: StatusSample | None) -> bool:
    if status is None:
        return False
    return bool(status.payload.lead_off_flags & (FLAG_LO_MINUS | FLAG_LO_PLUS))


def build_ecg_upload_batch(
    *,
    seq: int,
    window: tuple[EcgSample, ...],
    analysis: EcgAnalysisResult,
    last_sent_sample_index: int | None,
    lead_off: bool,
) -> UploadBatch | None:
    cleaned = analysis.cleaned
    if not cleaned:
        return None
    sample_window = window[-len(cleaned) :]
    if len(sample_window) != len(cleaned):
        return None

    pairs = [
        (sample, float(cleaned[index]))
        for index, sample in enumerate(sample_window)
        if last_sent_sample_index is None or sample.sample_index > last_sent_sample_index
    ]
    if not pairs:
        return None

    first_index = pairs[0][0].sample_index
    last_index = pairs[-1][0].sample_index
    r_peaks = tuple(
        sample_window[peak_index].sample_index - first_index
        for peak_index in analysis.r_peak_indices
        if 0 <= peak_index < len(sample_window)
        and first_index <= sample_window[peak_index].sample_index <= last_index
    )
    message = {
        "type": "ecg_batch",
        "seq": seq,
        "timestamp_ms": int(time.time() * 1000),
        "sample_rate": ECG_SAMPLE_RATE_HZ,
        "samples": [value for _sample, value in pairs],
        "r_peaks": list(r_peaks),
        "hr_bpm": _valid_hr(analysis.heart_rate_bpm),
        "sqi": _valid_sqi(analysis.signal_quality),
        "lead_off": lead_off,
    }
    return UploadBatch(message=message, last_sample_index=last_index)


class LiveIngestSocket:
    def __init__(self, *, ws_url: str, session_id: str, ingest_token: str) -> None:
        self.ws_url = ws_url
        self.session_id = session_id
        self.ingest_token = ingest_token
        self._socket: Any | None = None

    def close(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            finally:
                self._socket = None

    def send_json(self, message: dict[str, Any]) -> None:
        self._ensure_connected()
        assert self._socket is not None
        try:
            self._socket.send(json.dumps(message, separators=(",", ":")))
        except Exception:
            self.close()
            self._ensure_connected()
            assert self._socket is not None
            self._socket.send(json.dumps(message, separators=(",", ":")))

    def _ensure_connected(self) -> None:
        if self._socket is not None:
            return
        try:
            import websocket
        except ImportError as exc:  # pragma: no cover - optional runtime dependency
            raise RuntimeError("Install websocket-client for live upload: py -3.12 -m pip install -e .[upload]") from exc

        socket = websocket.create_connection(self.ws_url, timeout=8)
        socket.send(
            json.dumps(
                {
                    "type": "auth",
                    "role": "ingest",
                    "session_id": self.session_id,
                    "token": self.ingest_token,
                },
                separators=(",", ":"),
            )
        )
        reply = json.loads(socket.recv())
        if reply.get("type") != "auth_ok":
            socket.close()
            raise RuntimeError(f"Live ingest auth failed: {reply}")
        self._socket = socket


def run_live_upload(
    *,
    port: str,
    ws_url: str,
    session_id: str,
    ingest_token: str,
    raw_log_path: Path | None,
    interval_s: float,
) -> int:
    stores = PcDataStores.create()
    reader = SerialPacketReader(port=port, stores=stores, raw_log_path=raw_log_path)
    live = LiveIngestSocket(ws_url=ws_url, session_id=session_id, ingest_token=ingest_token)
    seq = 0
    status_seq = 0
    last_sent_sample_index: int | None = None
    next_status_at = 0.0
    reader.start()
    try:
        while True:
            now = time.monotonic()
            status = stores.status.latest()
            lead_off = lead_off_from_status(status)
            ecg_window = stores.ecg.snapshot()
            analysis = analyze_recent_ecg(ecg_window)
            batch = build_ecg_upload_batch(
                seq=seq,
                window=ecg_window,
                analysis=analysis,
                last_sent_sample_index=last_sent_sample_index,
                lead_off=lead_off,
            )
            if batch is not None:
                live.send_json(batch.message)
                last_sent_sample_index = batch.last_sample_index
                seq += 1
            if now >= next_status_at:
                stats = reader.stats
                live.send_json(
                    {
                        "type": "status",
                        "seq": status_seq,
                        "timestamp_ms": int(time.time() * 1000),
                        "hr_bpm": _valid_hr(analysis.heart_rate_bpm),
                        "sqi": _valid_sqi(analysis.signal_quality),
                        "lead_off": lead_off,
                        "packet_loss": stats.packets_lost,
                        "crc_errors": stats.crc_errors,
                        "note": analysis.message,
                    }
                )
                status_seq += 1
                next_status_at = now + 1.0
            if reader.last_error is not None:
                raise RuntimeError(f"Serial reader failed: {reader.last_error}") from reader.last_error
            time.sleep(interval_s)
    except KeyboardInterrupt:
        return 0
    finally:
        live.close()
        reader.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Upload PC-cleaned SmartCollar ECG to the Vercel live heartbeat service.")
    parser.add_argument("--port", required=True, help="Windows Bluetooth SPP COM port, for example COM19.")
    parser.add_argument("--ws-url", required=True, help="Live ingest WebSocket URL.")
    parser.add_argument("--session-id", required=True, help="Configured LIVE_SESSION_ID.")
    parser.add_argument("--ingest-token", required=True, help="Configured LIVE_INGEST_TOKEN.")
    parser.add_argument("--raw-log", default="", help="Optional local raw binary log path.")
    parser.add_argument("--interval", type=float, default=0.5, help="NeuroKit2 analysis/upload interval in seconds.")
    args = parser.parse_args(argv)

    raw_log_path = Path(args.raw_log) if args.raw_log else None
    return run_live_upload(
        port=args.port,
        ws_url=args.ws_url,
        session_id=args.session_id,
        ingest_token=args.ingest_token,
        raw_log_path=raw_log_path,
        interval_s=max(0.2, args.interval),
    )


if __name__ == "__main__":
    raise SystemExit(main())
