from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock, Thread
import time
from typing import Callable

from .buffers import EcgRingBuffer, ImuRingBuffer, StatusRingBuffer
from .protocol import (
    DeviceStatusPayload,
    EcgPayload,
    ImuPayload,
    PacketParser,
    ParserStats,
    ParsedPacket,
)
from .publisher import DataPublisher, NullPublisher
from .recorder import RawBinaryRecorder
from .source_coordinator import (
    CoordinatorResult,
    EcgSampleOrdinalExtender,
    PacketReceipt,
    SourceInstanceCoordinator,
    StagedPacket,
)


@dataclass(frozen=True, slots=True)
class SerialPortInfo:
    device: str
    description: str
    hwid: str
    is_bluetooth_candidate: bool
    is_bluetooth_outgoing: bool = False


@dataclass(slots=True)
class PcDataStores:
    ecg: EcgRingBuffer
    imu: ImuRingBuffer
    status: StatusRingBuffer

    @classmethod
    def create(cls) -> PcDataStores:
        return cls(
            ecg=EcgRingBuffer(),
            imu=ImuRingBuffer(),
            status=StatusRingBuffer(),
        )

    def clear(self) -> None:
        self.ecg.clear()
        self.imu.clear()
        self.status.clear()


@dataclass(frozen=True, slots=True)
class SerialRuntimeStatus:
    port: str
    started_at_monotonic_s: float | None
    serial_open: bool
    packet_count: int
    ecg_packet_count: int
    last_packet_monotonic_s: float | None
    last_ecg_sample_index: int | None
    source_instance_id: str
    last_transport_packet_monotonic_ns: int | None
    last_transport_packet_received_at_utc: str | None
    last_ecg_packet_monotonic_ns: int | None
    last_ecg_packet_received_at_utc: str | None
    last_status_packet_monotonic_ns: int | None
    last_status_packet_received_at_utc: str | None
    last_ecg_sample_ordinal: int | None
    last_ecg_raw_sample_index: int | None
    last_error: Exception | None


def list_serial_ports() -> list[SerialPortInfo]:
    try:
        from serial.tools import list_ports
    except ImportError as exc:  # pragma: no cover - depends on optional pyserial
        raise RuntimeError("pyserial is required for serial port enumeration") from exc

    ports: list[SerialPortInfo] = []
    for port in list_ports.comports():
        text = f"{port.description} {port.hwid}".lower()
        is_bluetooth = "bluetooth" in text or "bthenum" in text
        is_outgoing = is_bluetooth and "000000000000" not in text
        ports.append(
            SerialPortInfo(
                device=port.device,
                description=port.description,
                hwid=port.hwid,
                is_bluetooth_candidate=is_bluetooth,
                is_bluetooth_outgoing=is_outgoing,
            )
        )
    return ports


def bluetooth_candidate_ports() -> list[SerialPortInfo]:
    return [port for port in list_serial_ports() if port.is_bluetooth_candidate]


class SerialPacketReader:
    def __init__(
        self,
        *,
        port: str,
        baudrate: int = 115200,
        stores: PcDataStores | None = None,
        raw_log_path: str | Path | None = None,
        publisher: DataPublisher | None = None,
        raw_chunk_callback: Callable[[bytes], None] | None = None,
        receipt_factory: Callable[[], PacketReceipt] = PacketReceipt.now,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        source_coordinator: SourceInstanceCoordinator | None = None,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.stores = stores or PcDataStores.create()
        self.parser = PacketParser()
        self.publisher = publisher or NullPublisher()
        self.raw_log_path = Path(raw_log_path) if raw_log_path is not None else None
        self.raw_chunk_callback = raw_chunk_callback
        self.receipt_factory = receipt_factory
        self.monotonic_ns = monotonic_ns
        self.source_coordinator = source_coordinator or SourceInstanceCoordinator()
        self.ecg_ordinal_extender = EcgSampleOrdinalExtender()
        self._stop = Event()
        self._runtime_lock = Lock()
        self._thread: Thread | None = None
        self._recorder: RawBinaryRecorder | None = None
        self._last_error: Exception | None = None
        self._started_at_monotonic_s: float | None = None
        self._serial_open = False
        self._packet_count = 0
        self._ecg_packet_count = 0
        self._last_packet_monotonic_s: float | None = None
        self._last_ecg_sample_index: int | None = None
        self._last_transport_packet_monotonic_ns: int | None = None
        self._last_transport_packet_received_at_utc: str | None = None
        self._last_ecg_packet_monotonic_ns: int | None = None
        self._last_ecg_packet_received_at_utc: str | None = None
        self._last_status_packet_monotonic_ns: int | None = None
        self._last_status_packet_received_at_utc: str | None = None
        self._last_ecg_raw_sample_index: int | None = None

    @property
    def stats(self) -> ParserStats:
        return self.parser.stats

    @property
    def last_error(self) -> Exception | None:
        return self._last_error

    @property
    def runtime_status(self) -> SerialRuntimeStatus:
        with self._runtime_lock:
            return SerialRuntimeStatus(
                port=self.port,
                started_at_monotonic_s=self._started_at_monotonic_s,
                serial_open=self._serial_open,
                packet_count=self._packet_count,
                ecg_packet_count=self._ecg_packet_count,
                last_packet_monotonic_s=self._last_packet_monotonic_s,
                last_ecg_sample_index=self._last_ecg_sample_index,
                source_instance_id=self.source_coordinator.source_instance_id,
                last_transport_packet_monotonic_ns=self._last_transport_packet_monotonic_ns,
                last_transport_packet_received_at_utc=self._last_transport_packet_received_at_utc,
                last_ecg_packet_monotonic_ns=self._last_ecg_packet_monotonic_ns,
                last_ecg_packet_received_at_utc=self._last_ecg_packet_received_at_utc,
                last_status_packet_monotonic_ns=self._last_status_packet_monotonic_ns,
                last_status_packet_received_at_utc=self._last_status_packet_received_at_utc,
                last_ecg_sample_ordinal=self._last_ecg_sample_index,
                last_ecg_raw_sample_index=self._last_ecg_raw_sample_index,
                last_error=self._last_error,
            )

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self.source_coordinator.start_new_reader()
        self.parser.reset_sequence_baseline()
        self.ecg_ordinal_extender.reset()
        self.stores.clear()
        with self._runtime_lock:
            self._last_error = None
            self._started_at_monotonic_s = time.monotonic()
            self._serial_open = False
            self._packet_count = 0
            self._ecg_packet_count = 0
            self._last_packet_monotonic_s = None
            self._last_ecg_sample_index = None
            self._last_transport_packet_monotonic_ns = None
            self._last_transport_packet_received_at_utc = None
            self._last_ecg_packet_monotonic_ns = None
            self._last_ecg_packet_received_at_utc = None
            self._last_status_packet_monotonic_ns = None
            self._last_status_packet_received_at_utc = None
            self._last_ecg_raw_sample_index = None
        self._thread = Thread(target=self._run, name=f"SerialPacketReader-{self.port}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        if self._recorder is not None:
            self._recorder.close()
            self._recorder = None

    def feed_bytes(self, data: bytes) -> None:
        """Feed one arbitrary transport chunk for deterministic tests/replay."""

        if not data:
            self._flush_pending()
            return
        if self._recorder is not None:
            self._recorder.write(data)
        if self.raw_chunk_callback is not None:
            self.raw_chunk_callback(data)
        for packet in self.parser.feed(data):
            self._ingest(packet)

    def _run(self) -> None:
        try:
            import serial
        except ImportError as exc:  # pragma: no cover - depends on optional pyserial
            self._last_error = RuntimeError("pyserial is required for SPP virtual COM reading")
            self._last_error.__cause__ = exc
            return

        if self.raw_log_path is not None:
            self._recorder = RawBinaryRecorder(self.raw_log_path)

        try:
            with serial.Serial(self.port, self.baudrate, timeout=0.1) as serial_port:
                with self._runtime_lock:
                    self._serial_open = True
                while not self._stop.is_set():
                    chunk = serial_port.read(4096)
                    self.feed_bytes(chunk)
        except Exception as exc:  # pragma: no cover - hardware/OS path
            with self._runtime_lock:
                self._last_error = exc
        finally:
            with self._runtime_lock:
                self._serial_open = False
            if self._recorder is not None:
                self._recorder.close()
                self._recorder = None

    def _ingest(self, packet: ParsedPacket) -> None:
        staged = StagedPacket(packet=packet, receipt=self.receipt_factory())
        self._apply_coordinator_result(self.source_coordinator.ingest(staged))

    def _flush_pending(self) -> None:
        self._apply_coordinator_result(
            self.source_coordinator.flush_expired(self.monotonic_ns())
        )

    def _apply_coordinator_result(self, result: CoordinatorResult) -> None:
        if result.rotated:
            self.parser.reset_sequence_baseline()
            self.ecg_ordinal_extender.reset()
            self.stores.clear()
        for committed in result.committed:
            if self.parser.commit_packet(committed.packet):
                self._dispatch(committed)

    def _dispatch(self, staged: StagedPacket) -> None:
        packet = staged.packet
        receipt = staged.receipt
        payload = packet.payload
        if isinstance(payload, EcgPayload):
            try:
                ordinal = self.ecg_ordinal_extender.extend(payload)
            except (ValueError, OverflowError):
                ordinal = None
            if ordinal is not None:
                self.stores.ecg.append_batch(
                    packet.header,
                    payload,
                    source_instance_id=self.source_coordinator.source_instance_id,
                    receipt=receipt,
                    ordinal=ordinal,
                )
                with self._runtime_lock:
                    self._ecg_packet_count += 1
                    self._last_ecg_sample_index = ordinal.last_sample_ordinal
                    self._last_ecg_raw_sample_index = ordinal.raw_last_sample_index
                    self._last_ecg_packet_monotonic_ns = receipt.received_monotonic_ns
                    self._last_ecg_packet_received_at_utc = receipt.received_at_utc
        elif isinstance(payload, ImuPayload):
            self.stores.imu.append_batch(
                packet.header,
                payload,
                source_instance_id=self.source_coordinator.source_instance_id,
                receipt=receipt,
            )
        elif isinstance(payload, DeviceStatusPayload):
            self.stores.status.append(
                packet.header,
                payload,
                source_instance_id=self.source_coordinator.source_instance_id,
                receipt=receipt,
            )
            with self._runtime_lock:
                self._last_status_packet_monotonic_ns = receipt.received_monotonic_ns
                self._last_status_packet_received_at_utc = receipt.received_at_utc
        with self._runtime_lock:
            self._packet_count += 1
            self._last_packet_monotonic_s = receipt.received_monotonic_ns / 1_000_000_000
            self._last_transport_packet_monotonic_ns = receipt.received_monotonic_ns
            self._last_transport_packet_received_at_utc = receipt.received_at_utc
        self.publisher.publish_packet(packet)
