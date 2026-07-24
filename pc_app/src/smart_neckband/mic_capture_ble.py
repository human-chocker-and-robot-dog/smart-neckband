from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from queue import Empty, Queue
from threading import Event, Thread
from typing import Callable

from .mic_capture_protocol import MicFrame, MicFrameParser, ParserStats


BLE_UART_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
BLE_UART_RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
BLE_UART_TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"


@dataclass(frozen=True, slots=True)
class MicBleDevice:
    address: str
    name: str


def scan_mic_devices(timeout: float = 3.0) -> list[MicBleDevice]:
    try:
        from bleak import BleakScanner
    except ImportError as exc:
        raise RuntimeError("缺少 bleak；请先运行 .\\tools\\project.ps1 pc-setup") from exc

    async def discover() -> list[MicBleDevice]:
        found = await BleakScanner.discover(timeout=timeout, return_adv=True)
        devices: list[MicBleDevice] = []
        for device, advertisement in found.values():
            name = device.name or advertisement.local_name or "Unknown"
            if not name.lower().startswith("collarmic-"):
                continue
            devices.append(MicBleDevice(address=device.address, name=name))
        return sorted(devices, key=lambda item: (item.name.lower(), item.address))

    return asyncio.run(discover())


FrameCallback = Callable[[MicFrame, ParserStats], None]
StateCallback = Callable[[str], None]
ErrorCallback = Callable[[BaseException], None]


class MicBleClientThread(Thread):
    def __init__(
        self,
        address: str,
        *,
        on_frame: FrameCallback,
        on_state: StateCallback,
        on_error: ErrorCallback,
    ) -> None:
        super().__init__(name="mic-ble-client", daemon=True)
        self._address = address
        self._on_frame = on_frame
        self._on_state = on_state
        self._on_error = on_error
        self._commands: Queue[str] = Queue()
        self._stop_event = Event()
        self._parser = MicFrameParser()

    def send(self, command: str) -> None:
        self._commands.put(command.rstrip("\r\n") + "\n")

    def disconnect(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        try:
            asyncio.run(self._run_async())
        except BaseException as exc:
            self._on_error(exc)
        finally:
            self._on_state("disconnected")

    async def _run_async(self) -> None:
        try:
            from bleak import BleakClient, BleakScanner
        except ImportError as exc:
            raise RuntimeError("缺少 bleak；请先运行 .\\tools\\project.ps1 pc-setup") from exc

        self._on_state("connecting")
        device = await BleakScanner.find_device_by_address(self._address, timeout=10.0)
        if device is None:
            raise RuntimeError(f"未找到 BLE 设备 {self._address}，请重新扫描。")

        def notification(_sender: object, data: bytearray) -> None:
            for frame in self._parser.feed(data):
                self._on_frame(frame, replace(self._parser.stats))

        def disconnected(_client: object) -> None:
            self._stop_event.set()

        async with BleakClient(
            device,
            disconnected_callback=disconnected,
            timeout=20.0,
            winrt={"use_cached_services": False},
        ) as client:
            await client.start_notify(BLE_UART_TX_UUID, notification)
            self._on_state("connected")
            while not self._stop_event.is_set():
                try:
                    command = self._commands.get_nowait()
                except Empty:
                    await asyncio.sleep(0.03)
                    continue
                if command.strip().upper().startswith("START "):
                    self._parser.reset()
                await client.write_gatt_char(
                    BLE_UART_RX_UUID, command.encode("ascii"), response=True
                )
            try:
                await client.write_gatt_char(BLE_UART_RX_UUID, b"STOP\n", response=True)
                await client.stop_notify(BLE_UART_TX_UUID)
            except Exception:
                pass
