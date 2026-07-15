from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import logging
from pathlib import Path
from threading import Event, Lock, Thread
import time
from typing import Any
from urllib.parse import urlsplit

from .analysis import EcgAnalysisResult, analyze_recent_ecg
from .buffers import EcgSample, StatusSample
from .protocol import ECG_SAMPLE_RATE_HZ, FLAG_LO_MINUS, FLAG_LO_PLUS
from .serial_io import PcDataStores, SerialPacketReader


LOGGER = logging.getLogger("smart_neckband.live_uploader")


class IngestRejectedError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class UploadBatch:
    message: dict[str, Any]
    last_sample_index: int


@dataclass(frozen=True, slots=True)
class AnalysisFrame:
    generation: int
    window: tuple[EcgSample, ...]
    result: EcgAnalysisResult
    duration_s: float


class LiveEcgAnalysisWorker:
    def __init__(self, stores: PcDataStores, interval_s: float) -> None:
        self.stores = stores
        self.interval_s = interval_s
        self._stop = Event()
        self._lock = Lock()
        self._frame: AnalysisFrame | None = None
        self._last_error: Exception | None = None
        self._thread = Thread(target=self._run, name="LiveEcgAnalysisWorker", daemon=True)

    @property
    def last_error(self) -> Exception | None:
        return self._last_error

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def latest(self) -> AnalysisFrame | None:
        with self._lock:
            return self._frame

    def _run(self) -> None:
        try:
            warmup_started = time.monotonic()
            import neurokit2  # noqa: F401
            import numpy  # noqa: F401

            LOGGER.info("NeuroKit2 runtime ready warmup=%.2fs", time.monotonic() - warmup_started)
            generation = 0
            while not self._stop.is_set():
                window = self.stores.ecg.snapshot()
                analysis_started = time.monotonic()
                result = analyze_recent_ecg(window)
                generation += 1
                frame = AnalysisFrame(
                    generation=generation,
                    window=window,
                    result=result,
                    duration_s=time.monotonic() - analysis_started,
                )
                with self._lock:
                    self._frame = frame
                self._stop.wait(self.interval_s)
        except Exception as exc:  # pragma: no cover - dependency/runtime path
            self._last_error = exc


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
        self._next_sequence = 0

    @property
    def connected(self) -> bool:
        return self._socket is not None

    @property
    def next_sequence(self) -> int:
        return self._next_sequence

    def connect(self) -> int:
        self._ensure_connected()
        return self._next_sequence

    def close(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            finally:
                self._socket = None

    def send_json(self, message: dict[str, Any]) -> None:
        message_type = str(message.get("type", "unknown"))
        message_seq = message.get("seq")
        for attempt in range(2):
            try:
                self._ensure_connected()
                if message_type == "ecg_batch" and isinstance(message_seq, int) and message_seq < self._next_sequence:
                    LOGGER.warning(
                        "ECG seq=%d was already accepted before reconnect; server next_seq=%d",
                        message_seq,
                        self._next_sequence,
                    )
                    return
                assert self._socket is not None
                self._socket.send(json.dumps(message, separators=(",", ":")))
                reply = json.loads(self._socket.recv())
                if reply.get("type") == "error":
                    code = str(reply.get("code", "unknown"))
                    detail = str(reply.get("message", "ingest rejected"))
                    if code == "function_recycle":
                        raise ConnectionError(detail)
                    raise IngestRejectedError(f"{code}: {detail}")
                if reply.get("type") != "ingest_ack" or reply.get("message_type") != message_type:
                    raise IngestRejectedError(f"unexpected ingest reply: {reply}")
                next_sequence = reply.get("next_seq")
                if not isinstance(next_sequence, int) or next_sequence < 0:
                    raise IngestRejectedError(f"invalid next_seq in ingest acknowledgement: {reply}")
                self._next_sequence = next_sequence
                return
            except IngestRejectedError:
                self.close()
                raise
            except Exception as exc:
                self.close()
                if attempt > 0:
                    raise RuntimeError(f"{message_type} send failed after reconnect: {exc}") from exc
                LOGGER.warning("%s send interrupted (%s); reconnecting once", message_type, exc)

        raise RuntimeError(f"{message_type} send failed")

    def _ensure_connected(self) -> None:
        if self._socket is not None:
            return
        try:
            import websocket
        except ImportError as exc:  # pragma: no cover - optional runtime dependency
            raise RuntimeError("Install websocket-client for live upload: py -3.12 -m pip install -e .[upload]") from exc

        endpoint = urlsplit(self.ws_url)
        LOGGER.info("Connecting ingest WebSocket host=%s path=%s", endpoint.netloc, endpoint.path)
        socket = websocket.create_connection(self.ws_url, timeout=8)
        try:
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
                raise IngestRejectedError(f"live ingest auth failed: {reply}")
            next_sequence = reply.get("next_seq")
            if not isinstance(next_sequence, int) or next_sequence < 0:
                raise IngestRejectedError("live service did not provide a valid next_seq")
            self._next_sequence = next_sequence
            self._socket = socket
            LOGGER.info("Ingest authenticated session=%s next_seq=%d", self.session_id, self._next_sequence)
        except Exception:
            socket.close()
            raise


def run_live_upload(
    *,
    port: str,
    ws_url: str,
    session_id: str,
    ingest_token: str,
    raw_log_path: Path | None,
    interval_s: float,
    duration_s: float | None,
) -> int:
    stores = PcDataStores.create()
    reader = SerialPacketReader(port=port, stores=stores, raw_log_path=raw_log_path)
    analysis_worker = LiveEcgAnalysisWorker(stores=stores, interval_s=interval_s)
    live = LiveIngestSocket(ws_url=ws_url, session_id=session_id, ingest_token=ingest_token)
    seq = live.connect()
    status_seq = 0
    last_sent_sample_index: int | None = None
    next_status_at = 0.0
    started_at = time.monotonic()
    next_progress_at = started_at
    warned_no_packets = False
    uploaded_batches = 0
    uploaded_samples = 0
    last_analysis_generation = 0
    analysis_frame: AnalysisFrame | None = None
    analysis = analyze_recent_ecg(())
    LOGGER.info(
        "Starting live upload port=%s interval=%.2fs duration=%s raw_log=%s",
        port,
        interval_s,
        "unbounded" if duration_s is None else f"{duration_s:.1f}s",
        raw_log_path if raw_log_path is not None else "disabled",
    )
    reader.start()
    analysis_worker.start()
    try:
        while True:
            now = time.monotonic()
            elapsed = now - started_at
            if duration_s is not None and elapsed >= duration_s:
                LOGGER.info("Debug duration reached after %.1fs", elapsed)
                return 0
            if reader.last_error is not None:
                raise RuntimeError(f"Serial reader failed: {reader.last_error}") from reader.last_error
            if analysis_worker.last_error is not None:
                raise RuntimeError(f"ECG analysis worker failed: {analysis_worker.last_error}") from analysis_worker.last_error
            status = stores.status.latest()
            lead_off = lead_off_from_status(status)
            ecg_window = stores.ecg.snapshot()
            latest_frame = analysis_worker.latest()
            if latest_frame is not None:
                analysis_frame = latest_frame
                analysis = latest_frame.result
            if analysis_frame is not None and analysis_frame.generation != last_analysis_generation:
                batch = build_ecg_upload_batch(
                    seq=seq,
                    window=analysis_frame.window,
                    analysis=analysis_frame.result,
                    last_sent_sample_index=last_sent_sample_index,
                    lead_off=lead_off,
                )
                last_analysis_generation = analysis_frame.generation
                if batch is not None:
                    live.send_json(batch.message)
                    last_sent_sample_index = batch.last_sample_index
                    seq += 1
                    uploaded_batches += 1
                    uploaded_samples += len(batch.message["samples"])
            stats = reader.stats
            if now >= next_status_at and stats.packets_ok > 0:
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
            if now >= next_progress_at:
                LOGGER.info(
                    "health elapsed=%.1fs serial_packets=%d ecg_buffer=%d uploaded_batches=%d uploaded_samples=%d "
                    "hr=%s sqi=%s lead_off=%s loss=%d crc=%d analysis_ms=%s analysis=%s",
                    elapsed,
                    stats.packets_ok,
                    len(ecg_window),
                    uploaded_batches,
                    uploaded_samples,
                    "--" if analysis.heart_rate_bpm is None else f"{analysis.heart_rate_bpm:.1f}",
                    "--" if analysis.signal_quality is None else f"{analysis.signal_quality:.2f}",
                    lead_off,
                    stats.packets_lost,
                    stats.crc_errors,
                    "--" if analysis_frame is None else f"{analysis_frame.duration_s * 1000:.0f}",
                    analysis.message,
                )
                next_progress_at = now + 1.0
            if not warned_no_packets and elapsed >= 3.0 and stats.packets_ok == 0:
                warned_no_packets = True
                LOGGER.warning("No valid SmartCollar packets received from %s after %.1fs", port, elapsed)
            time.sleep(0.1)
    except KeyboardInterrupt:
        LOGGER.info("Stopped by user")
        return 0
    finally:
        live.close()
        analysis_worker.stop()
        reader.stop()
        LOGGER.info("Uploader stopped")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Upload PC-cleaned SmartCollar ECG to the Vercel live heartbeat service.")
    parser.add_argument("--port", required=True, help="Windows Bluetooth SPP COM port, for example COM19.")
    parser.add_argument("--ws-url", required=True, help="Live ingest WebSocket URL.")
    parser.add_argument("--session-id", required=True, help="Configured LIVE_SESSION_ID.")
    parser.add_argument("--ingest-token", required=True, help="Configured LIVE_INGEST_TOKEN.")
    parser.add_argument("--raw-log", default="", help="Optional local raw binary log path.")
    parser.add_argument("--interval", type=float, default=0.5, help="NeuroKit2 analysis/upload interval in seconds.")
    parser.add_argument("--duration", type=float, default=0.0, help="Optional bounded run duration in seconds; 0 runs until Ctrl+C.")
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    raw_log_path = Path(args.raw_log) if args.raw_log else None
    try:
        return run_live_upload(
            port=args.port,
            ws_url=args.ws_url,
            session_id=args.session_id,
            ingest_token=args.ingest_token,
            raw_log_path=raw_log_path,
            interval_s=max(0.2, args.interval),
            duration_s=args.duration if args.duration > 0 else None,
        )
    except Exception:
        LOGGER.exception("Live uploader failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
