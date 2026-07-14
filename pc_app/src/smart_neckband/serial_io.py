from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Event, Thread

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
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.stores = stores or PcDataStores.create()
        self.parser = PacketParser()
        self.publisher = publisher or NullPublisher()
        self.raw_log_path = Path(raw_log_path) if raw_log_path is not None else None
        self._stop = Event()
        self._thread: Thread | None = None
        self._recorder: RawBinaryRecorder | None = None
        self._last_error: Exception | None = None

    @property
    def stats(self) -> ParserStats:
        return self.parser.stats

    @property
    def last_error(self) -> Exception | None:
        return self._last_error

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(target=self._run, name=f"SerialPacketReader-{self.port}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        if self._recorder is not None:
            self._recorder.close()
            self._recorder = None

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
                while not self._stop.is_set():
                    chunk = serial_port.read(4096)
                    if not chunk:
                        continue
                    if self._recorder is not None:
                        self._recorder.write(chunk)
                    for packet in self.parser.feed(chunk):
                        self._dispatch(packet)
        except Exception as exc:  # pragma: no cover - hardware/OS path
            self._last_error = exc
        finally:
            if self._recorder is not None:
                self._recorder.close()
                self._recorder = None

    def _dispatch(self, packet: ParsedPacket) -> None:
        payload = packet.payload
        if isinstance(payload, EcgPayload):
            self.stores.ecg.append_batch(packet.header, payload)
        elif isinstance(payload, ImuPayload):
            self.stores.imu.append_batch(packet.header, payload)
        elif isinstance(payload, DeviceStatusPayload):
            self.stores.status.append(packet.header, payload)
        self.publisher.publish_packet(packet)
