from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from pathlib import Path
import sys
from threading import Event, Lock, Thread
import time
from typing import Callable

from .protocol import DeviceStatusPayload, EcgPayload, ImuPayload, PacketParser, ParserStats, ParsedPacket
from .publisher import DataPublisher, NullPublisher
from .recorder import RawBinaryRecorder
from .serial_io import PcDataStores, SerialRuntimeStatus


BLE_UART_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
BLE_UART_RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
BLE_UART_TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
BLE_AUTH_SETTLE_ATTEMPTS = 20
BLE_AUTH_SETTLE_DELAY_S = 0.25
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BleDeviceInfo:
    address: str
    name: str


def list_ble_devices(timeout: float = 2.0) -> list[BleDeviceInfo]:
    """Scan synchronously for GUI use; Bleak stays an optional dependency."""

    LOGGER.debug("BLE scan started; timeout=%.1f seconds", timeout)
    try:
        from bleak import BleakScanner
    except ImportError as exc:  # pragma: no cover - depends on optional Bleak
        raise RuntimeError(
            "Bleak 未安装在当前 Python 环境；"
            f"当前解释器：{sys.executable}。请运行 .\\tools\\project.ps1 pc-setup，"
            "然后使用 .\\tools\\project.ps1 pc-gui 启动上位机"
        ) from exc

    async def discover() -> list[BleDeviceInfo]:
        discovered = await BleakScanner.discover(timeout=timeout, return_adv=True)
        result: list[BleDeviceInfo] = []
        for device, advertisement in discovered.values():
            name = device.name or advertisement.local_name or "Unknown BLE device"
            advertised_services = advertisement.service_uuids or []
            if not _matches_ble_uart_device(name, advertised_services):
                continue
            result.append(BleDeviceInfo(address=device.address, name=name))
        return sorted(result, key=lambda item: (item.name, item.address))

    devices = asyncio.run(discover())
    LOGGER.debug(
        "BLE scan completed; matching devices=%s",
        [(device.name, device.address) for device in devices],
    )
    return devices


def _matches_ble_uart_device(name: str, service_uuids: list[str]) -> bool:
    normalized_services = {value.lower() for value in service_uuids}
    return BLE_UART_SERVICE_UUID in normalized_services or name.lower().startswith("collarc3-")


async def _start_notify_after_bond(
    client: object,
    callback: Callable[[object, bytearray], None],
    *,
    attempts: int = BLE_AUTH_SETTLE_ATTEMPTS,
    delay_s: float = BLE_AUTH_SETTLE_DELAY_S,
) -> None:
    """Wait for bonded-link encryption before writing the TX CCCD."""

    for attempt in range(attempts):
        try:
            LOGGER.debug("BLE TX notify attempt %d/%d", attempt + 1, attempts)
            await client.start_notify(BLE_UART_TX_UUID, callback)  # type: ignore[attr-defined]
            LOGGER.debug("BLE TX notifications enabled")
            return
        except Exception as exc:
            authentication_pending = "Insufficient Authentication" in str(exc)
            LOGGER.debug(
                "BLE TX notify attempt %d failed: %s",
                attempt + 1,
                exc,
            )
            if not authentication_pending or attempt + 1 >= attempts:
                raise
            await asyncio.sleep(delay_s)


class BlePacketReader:
    """Receive the V0 byte stream from the ESP-IDF BLE UART TX characteristic.

    ESP-IDF fragments writes to the live ATT MTU. Notifications are therefore
    treated as arbitrary ordered byte chunks and fed directly to PacketParser;
    V0 magic, length, CRC, and packet sequence provide stream recovery.
    """

    def __init__(
        self,
        *,
        address: str,
        stores: PcDataStores | None = None,
        raw_log_path: str | Path | None = None,
        publisher: DataPublisher | None = None,
        raw_chunk_callback: Callable[[bytes], None] | None = None,
        debug_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.address = address
        self.port = address
        self.stores = stores or PcDataStores.create()
        self.parser = PacketParser()
        self.publisher = publisher or NullPublisher()
        self.raw_log_path = Path(raw_log_path) if raw_log_path is not None else None
        self.raw_chunk_callback = raw_chunk_callback
        self.debug_callback = debug_callback
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
        self._first_notification_logged = False

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
                port=self.address,
                started_at_monotonic_s=self._started_at_monotonic_s,
                serial_open=self._serial_open,
                packet_count=self._packet_count,
                ecg_packet_count=self._ecg_packet_count,
                last_packet_monotonic_s=self._last_packet_monotonic_s,
                last_ecg_sample_index=self._last_ecg_sample_index,
                last_error=self._last_error,
            )

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            self._debug("启动请求已忽略：BLE 读取线程已在运行")
            return
        self._stop.clear()
        with self._runtime_lock:
            self._last_error = None
            self._started_at_monotonic_s = time.monotonic()
            self._serial_open = False
            self._packet_count = 0
            self._ecg_packet_count = 0
            self._last_packet_monotonic_s = None
            self._last_ecg_sample_index = None
            self._first_notification_logged = False
        self._thread = Thread(target=self._run, name=f"BlePacketReader-{self.address}", daemon=True)
        self._debug("启动 BLE 连接线程，设备=%s", self.address)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._debug("请求断开 BLE，等待 %.1f 秒", timeout)
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                self._debug("警告：BLE 线程未在超时内退出")
            else:
                self._debug("BLE 线程已退出")
        if self._recorder is not None:
            self._recorder.close()
            self._recorder = None

    def feed_notification(self, data: bytes) -> None:
        """Feed one ATT notification; public to allow deterministic tests."""

        if not data:
            return
        if self._recorder is not None:
            self._recorder.write(data)
        if self.raw_chunk_callback is not None:
            self.raw_chunk_callback(data)
        for packet in self.parser.feed(data):
            self._dispatch(packet)

    def _run(self) -> None:
        try:
            self._debug("BLE asyncio 事件循环启动")
            asyncio.run(self._run_async())
        except Exception as exc:  # pragma: no cover - hardware/OS path
            LOGGER.exception("BLE connection failed for %s", self.address)
            self._debug("BLE 连接失败：%s: %s", type(exc).__name__, exc)
            with self._runtime_lock:
                self._last_error = exc
        finally:
            with self._runtime_lock:
                self._serial_open = False
            if self._recorder is not None:
                self._recorder.close()
                self._recorder = None
            self._debug("BLE 连接流程结束")

    async def _run_async(self) -> None:
        self._debug("加载 Bleak Windows 后端")
        try:
            from bleak import BleakClient
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "Bleak 未安装在当前 Python 环境；"
                f"当前解释器：{sys.executable}。请运行 .\\tools\\project.ps1 pc-setup，"
                "然后使用 .\\tools\\project.ps1 pc-gui 启动上位机"
            ) from exc

        if self.raw_log_path is not None:
            self._recorder = RawBinaryRecorder(self.raw_log_path)

        def disconnected(_: object) -> None:
            self._debug("收到 BLE 断开回调")
            with self._runtime_lock:
                self._serial_open = False

        def notification(_: object, data: bytearray) -> None:
            # WinRT may emit a transient disconnected callback while the
            # pair=True connection is being replaced by its bonded GATT
            # session. Receiving a notification is stronger evidence that
            # the final session is live, so repair the UI/runtime flag here.
            with self._runtime_lock:
                first_notification = not self._first_notification_logged
                self._first_notification_logged = True
                self._serial_open = True
            if first_notification:
                self._debug("收到首个 BLE notification，长度=%d 字节", len(data))
            self.feed_notification(bytes(data))

        # Ask the backend to pair as part of connect. The firmware initiates
        # security immediately on GAP connect, so calling pair() only after
        # connect creates a Windows/NimBLE pairing race.
        self._debug("开始连接并自动配对，超时=20 秒，禁用 WinRT GATT 缓存")
        async with BleakClient(
            self.address,
            disconnected_callback=disconnected,
            timeout=20.0,
            pair=True,
            winrt={"use_cached_services": False},
        ) as client:
            if not client.is_connected:
                raise RuntimeError(f"failed to connect to BLE device {self.address}")

            service_uuids = ", ".join(str(service.uuid) for service in client.services)
            self._debug("BLE 已连接并完成配对阶段；GATT 服务=%s", service_uuids or "<none>")
            self._debug("等待绑定链路加密并订阅 TX notification")
            await _start_notify_after_bond(client, notification)
            with self._runtime_lock:
                self._serial_open = True
            self._debug("BLE 数据通道已就绪")

            while not self._stop.is_set() and client.is_connected:
                await asyncio.sleep(0.1)

            if client.is_connected:
                self._debug("停止 TX notification")
                await client.stop_notify(BLE_UART_TX_UUID)

    def _debug(self, message: str, *args: object) -> None:
        rendered = message % args if args else message
        LOGGER.debug(rendered)
        if self.debug_callback is not None:
            try:
                self.debug_callback(rendered)
            except Exception:
                LOGGER.exception("BLE debug callback failed")

    def _dispatch(self, packet: ParsedPacket) -> None:
        payload = packet.payload
        if isinstance(payload, EcgPayload):
            self.stores.ecg.append_batch(packet.header, payload)
            with self._runtime_lock:
                self._ecg_packet_count += 1
                self._last_ecg_sample_index = payload.first_sample_index + max(0, len(payload.samples) - 1)
        elif isinstance(payload, ImuPayload):
            self.stores.imu.append_batch(packet.header, payload)
        elif isinstance(payload, DeviceStatusPayload):
            self.stores.status.append(packet.header, payload)
        with self._runtime_lock:
            self._packet_count += 1
            self._last_packet_monotonic_s = time.monotonic()
        self.publisher.publish_packet(packet)
