from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from threading import Event, Lock, Thread
import time
from typing import Callable

from .epaper_protocol import (
    CONTROL_UUID,
    DEFAULT_CHUNK_DATA_BYTES,
    DEVICE_INFO_UUID,
    DISPLAY_SERVICE_UUID,
    FRAME_BYTES,
    FRAME_DATA_UUID,
    STATUS_UUID,
    BeginFrame,
    ControlCommand,
    DeviceCapability,
    DeviceInfo,
    DisplayStateCode,
    DisplayStatus,
    RefreshRequest,
    StatusFlag,
    decode_device_info,
    decode_status,
    encode_begin_frame,
    encode_frame_id_control,
    frame_crc32,
    iter_frame_chunks,
)
from .epaper_sync import ScheduledEpaperFrame


LOGGER = logging.getLogger(__name__)
AUTH_SETTLE_ATTEMPTS = 6
AUTH_SETTLE_DELAY_S = 0.5
LINK_SECURITY_TIMEOUT_S = 20.0
LINK_SECURITY_POLL_INTERVAL_S = 0.25
FRAME_COMPLETION_TIMEOUT_S = 45.0


@dataclass(frozen=True, slots=True)
class EpaperDevice:
    address: str
    name: str


@dataclass(frozen=True, slots=True)
class EpaperRuntimeStatus:
    address: str
    started_at_monotonic_s: float | None
    connected: bool
    device_info: DeviceInfo | None
    display_status: DisplayStatus | None
    pending_frame_id: int | None
    active_frame_id: int | None
    frame_sent_count: int
    frame_completed_count: int
    local_pending_replaced_count: int
    last_error: Exception | None


@dataclass(frozen=True, slots=True)
class _FrameRequest:
    frame_id: int
    scheduled: ScheduledEpaperFrame
    attempts: int = 0


class DisplayRejectedError(RuntimeError):
    pass


class DisplayAuthenticationError(RuntimeError):
    pass


class LinkSecurityTimeoutError(TimeoutError):
    pass


def _matches_epaper_device(name: str, service_uuids: list[str]) -> bool:
    normalized = {value.lower() for value in service_uuids}
    return DISPLAY_SERVICE_UUID in normalized or name.lower().startswith(
        "inkcanvas-quote0-"
    )


def list_epaper_devices(timeout: float = 2.0) -> list[EpaperDevice]:
    try:
        from bleak import BleakScanner
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "Bleak 未安装；请运行 .\\tools\\project.ps1 pc-setup 后重试"
        ) from exc

    async def discover() -> list[EpaperDevice]:
        discovered = await BleakScanner.discover(timeout=timeout, return_adv=True)
        result: list[EpaperDevice] = []
        for device, advertisement in discovered.values():
            name = device.name or advertisement.local_name or "Unknown BLE device"
            services = advertisement.service_uuids or []
            if _matches_epaper_device(name, services):
                result.append(EpaperDevice(address=device.address, name=name))
        return sorted(result, key=lambda value: (value.name, value.address))

    return asyncio.run(discover())


async def _start_status_notify_after_bond(
    client: object,
    callback: Callable[[object, bytearray], None],
    *,
    attempts: int = AUTH_SETTLE_ATTEMPTS,
    delay_s: float = AUTH_SETTLE_DELAY_S,
) -> None:
    for attempt in range(attempts):
        try:
            await client.start_notify(STATUS_UUID, callback)  # type: ignore[attr-defined]
            return
        except Exception as exc:
            authentication_pending = "Insufficient Authentication" in str(exc)
            if not authentication_pending or attempt + 1 >= attempts:
                raise
            await asyncio.sleep(delay_s)


async def _wait_for_link_security(
    client: object,
    *,
    timeout_s: float = LINK_SECURITY_TIMEOUT_S,
    poll_interval_s: float = LINK_SECURITY_POLL_INTERVAL_S,
) -> DisplayStatus:
    required = StatusFlag.LINK_ENCRYPTED | StatusFlag.LINK_BONDED
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s

    while loop.time() < deadline:
        status = decode_status(
            bytes(await client.read_gatt_char(STATUS_UUID))  # type: ignore[attr-defined]
        )
        if status.flags & required == required:
            return status
        await asyncio.sleep(poll_interval_s)

    raise LinkSecurityTimeoutError(
        "Quote/0 BLE encryption/bonding did not complete"
    )


def _is_insufficient_authentication(exc: Exception) -> bool:
    code = getattr(exc, "code", None)
    try:
        if code is not None and int(code) == 5:
            return True
    except (TypeError, ValueError):
        pass
    return bool(exc.args and exc.args[0] == 5) or (
        "Insufficient Authentication" in str(exc)
    )


class EpaperDisplayClient:
    def __init__(
        self,
        *,
        address: str,
        debug_callback: Callable[[str], None] | None = None,
        client_factory: Callable[..., object] | None = None,
        completion_timeout_s: float = FRAME_COMPLETION_TIMEOUT_S,
        auto_reconnect: bool = True,
        reconnect_delay_s: float = 2.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.address = address
        self.debug_callback = debug_callback
        self.client_factory = client_factory
        self.completion_timeout_s = completion_timeout_s
        self.auto_reconnect = auto_reconnect
        self.reconnect_delay_s = reconnect_delay_s
        self.monotonic = monotonic
        self._stop = Event()
        self._runtime_lock = Lock()
        self._pending_lock = Lock()
        self._thread: Thread | None = None
        self._pending: _FrameRequest | None = None
        self._next_frame_id = 1
        self._started_at_monotonic_s: float | None = None
        self._connected = False
        self._device_info: DeviceInfo | None = None
        self._display_status: DisplayStatus | None = None
        self._active_frame_id: int | None = None
        self._frame_sent_count = 0
        self._frame_completed_count = 0
        self._local_pending_replaced_count = 0
        self._last_error: Exception | None = None
        self._last_completed_frame_id: int | None = None
        self._completion_events: dict[int, asyncio.Event] = {}

    @property
    def runtime_status(self) -> EpaperRuntimeStatus:
        with self._pending_lock:
            pending_id = self._pending.frame_id if self._pending is not None else None
        with self._runtime_lock:
            return EpaperRuntimeStatus(
                address=self.address,
                started_at_monotonic_s=self._started_at_monotonic_s,
                connected=self._connected,
                device_info=self._device_info,
                display_status=self._display_status,
                pending_frame_id=pending_id,
                active_frame_id=self._active_frame_id,
                frame_sent_count=self._frame_sent_count,
                frame_completed_count=self._frame_completed_count,
                local_pending_replaced_count=self._local_pending_replaced_count,
                last_error=self._last_error,
            )

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        with self._runtime_lock:
            self._started_at_monotonic_s = self.monotonic()
            self._last_error = None
        self._thread = Thread(
            target=self._run,
            name=f"EpaperDisplayClient-{self.address}",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def queue_frame(self, scheduled: ScheduledEpaperFrame) -> int:
        if len(scheduled.frame) != FRAME_BYTES:
            raise ValueError(f"frame must contain {FRAME_BYTES} bytes")
        actual_crc = frame_crc32(scheduled.frame)
        if actual_crc != scheduled.crc32:
            raise ValueError("scheduled frame CRC does not match its bytes")
        with self._pending_lock:
            frame_id = self._next_frame_id
            self._next_frame_id = (self._next_frame_id + 1) & 0xFFFFFFFF
            if self._next_frame_id == 0:
                self._next_frame_id = 1
            if self._pending is not None:
                with self._runtime_lock:
                    self._local_pending_replaced_count += 1
            self._pending = _FrameRequest(frame_id=frame_id, scheduled=scheduled)
        return frame_id

    def _take_pending(self) -> _FrameRequest | None:
        with self._pending_lock:
            value = self._pending
            self._pending = None
            return value

    def _restore_pending_after_transient_failure(self, request: _FrameRequest) -> None:
        if request.attempts >= 1:
            return
        retry = _FrameRequest(
            frame_id=request.frame_id,
            scheduled=request.scheduled,
            attempts=request.attempts + 1,
        )
        with self._pending_lock:
            if self._pending is None:
                self._pending = retry

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                asyncio.run(self._run_async())
            except Exception as exc:  # pragma: no cover - Windows/hardware path
                LOGGER.exception("Quote/0 BLE connection failed for %s", self.address)
                self._set_error(exc)
                self._debug("墨水屏 BLE 连接失败：%s: %s", type(exc).__name__, exc)
            finally:
                with self._runtime_lock:
                    self._connected = False
                    self._active_frame_id = None
            if not self.auto_reconnect or self._stop.is_set():
                return
            self._debug("%.1f 秒后重新连接墨水屏", self.reconnect_delay_s)
            self._stop.wait(self.reconnect_delay_s)

    async def _run_async(self) -> None:
        factory = self.client_factory
        if factory is None:
            try:
                from bleak import BleakClient
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise RuntimeError(
                    "Bleak 未安装；请运行 .\\tools\\project.ps1 pc-setup 后重试"
                ) from exc
            factory = BleakClient

        def disconnected(_client: object) -> None:
            self._debug("墨水屏 BLE 已断开")
            with self._runtime_lock:
                self._connected = False

        def status_notification(_sender: object, data: bytearray) -> None:
            self._handle_status_bytes(bytes(data))

        self._debug("连接墨水屏 %s", self.address)
        async with factory(
            self.address,
            disconnected_callback=disconnected,
            timeout=20.0,
            pair=True,
            winrt={"use_cached_services": False},
        ) as client:
            if not client.is_connected:  # type: ignore[attr-defined]
                raise RuntimeError(f"failed to connect to Quote/0 {self.address}")
            info = decode_device_info(
                bytes(await client.read_gatt_char(DEVICE_INFO_UUID))  # type: ignore[attr-defined]
            )
            if (info.width, info.height, info.frame_bytes) != (296, 152, FRAME_BYTES):
                raise RuntimeError(
                    "Quote/0 reports an incompatible framebuffer: "
                    f"{info.width}x{info.height}/{info.frame_bytes}"
                )
            if info.status_size != 64 or info.begin_frame_size != 32:
                raise RuntimeError(
                    "Quote/0 reports incompatible protocol structure sizes: "
                    f"status={info.status_size}, begin={info.begin_frame_size}"
                )
            if not info.capabilities & DeviceCapability.ENCRYPTED_WRITES:
                raise RuntimeError("Quote/0 does not require encrypted display writes")
            if not info.capabilities & DeviceCapability.STATUS_NOTIFY:
                raise RuntimeError("Quote/0 does not support display status notifications")
            await _start_status_notify_after_bond(client, status_notification)
            security_status = await _wait_for_link_security(client)
            self._record_status(security_status, count_completion=False)
            with self._runtime_lock:
                self._connected = True
                self._device_info = info
            self._debug(
                "墨水屏 BLE 已连接且链路已加密/绑定：firmware=%s chunk=%d",
                info.firmware_version,
                info.max_chunk_payload,
            )

            while not self._stop.is_set() and client.is_connected:  # type: ignore[attr-defined]
                request = self._take_pending()
                if request is not None:
                    try:
                        await self._send_frame(client, request, info)
                    except Exception as exc:
                        if not isinstance(
                            exc,
                            (
                                DisplayRejectedError,
                                DisplayAuthenticationError,
                                LinkSecurityTimeoutError,
                            ),
                        ):
                            self._restore_pending_after_transient_failure(request)
                        self._set_error(exc)
                        self._debug(
                            "墨水屏帧 %d 发送失败：%s: %s",
                            request.frame_id,
                            type(exc).__name__,
                            exc,
                        )
                await asyncio.sleep(0.05)

            if client.is_connected:  # type: ignore[attr-defined]
                await client.stop_notify(STATUS_UUID)  # type: ignore[attr-defined]

    async def _send_frame(
        self,
        client: object,
        request: _FrameRequest,
        info: DeviceInfo,
    ) -> None:
        scheduled = request.scheduled
        refresh_request = (
            RefreshRequest.FORCE_FULL
            if scheduled.force_full
            else RefreshRequest.AUTO
        )
        begin = encode_begin_frame(
            BeginFrame(
                frame_id=request.frame_id,
                crc32=scheduled.crc32,
                source_sample_index=scheduled.source_sample_index,
                source_timestamp_us=scheduled.source_timestamp_us,
                refresh_request=refresh_request,
            )
        )
        completion = asyncio.Event()
        self._completion_events[request.frame_id] = completion
        with self._runtime_lock:
            self._active_frame_id = request.frame_id
            self._last_error = None
        begin_accepted = False
        try:
            await self._write_begin_with_security_retry(client, begin)
            begin_accepted = True
            chunk_size = max(
                1, min(DEFAULT_CHUNK_DATA_BYTES, info.max_chunk_payload)
            )
            for packet in iter_frame_chunks(
                frame_id=request.frame_id,
                frame=scheduled.frame,
                chunk_data_bytes=chunk_size,
            ):
                if self._stop.is_set():
                    raise RuntimeError("display client is stopping")
                await client.write_gatt_char(FRAME_DATA_UUID, packet, response=True)  # type: ignore[attr-defined]
            await client.write_gatt_char(  # type: ignore[attr-defined]
                CONTROL_UUID,
                encode_frame_id_control(ControlCommand.COMMIT_FRAME, request.frame_id),
                response=True,
            )
            with self._runtime_lock:
                self._frame_sent_count += 1
            await self._wait_for_completion(request.frame_id, completion)
        except Exception:
            if begin_accepted:
                try:
                    await client.write_gatt_char(  # type: ignore[attr-defined]
                        CONTROL_UUID,
                        encode_frame_id_control(
                            ControlCommand.CANCEL_FRAME, request.frame_id
                        ),
                        response=True,
                    )
                except Exception:
                    pass
            raise
        finally:
            self._completion_events.pop(request.frame_id, None)
            with self._runtime_lock:
                if self._active_frame_id == request.frame_id:
                    self._active_frame_id = None

    async def _write_begin_with_security_retry(
        self,
        client: object,
        begin: bytes,
    ) -> None:
        try:
            await client.write_gatt_char(CONTROL_UUID, begin, response=True)  # type: ignore[attr-defined]
            return
        except Exception as exc:
            if not _is_insufficient_authentication(exc):
                raise

        await self._record_current_link_security(client, "BEGIN 被拒绝")
        self._debug("墨水屏 BEGIN 认证不足，重新请求配对并等待链路加密")
        try:
            await client.pair()  # type: ignore[attr-defined]
            security_status = await _wait_for_link_security(client)
        except LinkSecurityTimeoutError:
            raise
        except Exception as exc:
            raise DisplayAuthenticationError(
                "Quote/0 BLE re-pairing failed after authentication error"
            ) from exc
        self._record_status(security_status, count_completion=False)
        self._debug_link_security("重新配对后", security_status)

        try:
            await client.write_gatt_char(CONTROL_UUID, begin, response=True)  # type: ignore[attr-defined]
        except Exception as exc:
            if _is_insufficient_authentication(exc):
                await self._record_current_link_security(client, "BEGIN 重试仍被拒绝")
                raise DisplayAuthenticationError(
                    "Quote/0 BEGIN remained unauthenticated after one re-pair attempt"
                ) from exc
            raise

    async def _record_current_link_security(
        self,
        client: object,
        context: str,
    ) -> None:
        try:
            status = decode_status(
                bytes(await client.read_gatt_char(STATUS_UUID))  # type: ignore[attr-defined]
            )
        except Exception as exc:
            self._debug("%s，STATUS 读取失败：%s", context, exc)
            return
        self._record_status(status, count_completion=False)
        self._debug_link_security(context, status)

    def _debug_link_security(self, context: str, status: DisplayStatus) -> None:
        self._debug(
            "%s：encrypted=%s bonded=%s connected=%s flags=0x%04x",
            context,
            bool(status.flags & StatusFlag.LINK_ENCRYPTED),
            bool(status.flags & StatusFlag.LINK_BONDED),
            bool(status.flags & StatusFlag.LINK_CONNECTED),
            int(status.flags),
        )

    async def _wait_for_completion(
        self,
        frame_id: int,
        completion: asyncio.Event,
    ) -> None:
        deadline = self.monotonic() + self.completion_timeout_s
        while not completion.is_set():
            if self._stop.is_set():
                raise RuntimeError("display client stopped before frame completion")
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Quote/0 frame {frame_id} completion timed out")
            try:
                await asyncio.wait_for(completion.wait(), timeout=min(0.2, remaining))
            except asyncio.TimeoutError:
                continue
        status = self.runtime_status.display_status
        if status is None or status.last_frame_id != frame_id:
            raise RuntimeError("Quote/0 completion notification lost its frame ID")
        if status.state is DisplayStateCode.ERROR:
            raise DisplayRejectedError(
                f"Quote/0 rejected frame {frame_id}: {status.last_error.name}"
            )

    def _handle_status_bytes(
        self,
        data: bytes,
        *,
        count_completion: bool = True,
    ) -> None:
        try:
            status = decode_status(data)
        except Exception as exc:
            self._set_error(exc)
            self._debug("忽略无效墨水屏 STATUS：%s", exc)
            return
        self._record_status(status, count_completion=count_completion)

    def _record_status(
        self,
        status: DisplayStatus,
        *,
        count_completion: bool = True,
    ) -> None:
        with self._runtime_lock:
            self._display_status = status
            if (
                status.state is DisplayStateCode.DONE
                and status.last_frame_id != 0
                and status.last_frame_id != self._last_completed_frame_id
            ):
                if count_completion:
                    self._frame_completed_count += 1
                self._last_completed_frame_id = status.last_frame_id
        event = self._completion_events.get(status.last_frame_id)
        if event is not None and status.state in {
            DisplayStateCode.DONE,
            DisplayStateCode.ERROR,
        }:
            event.set()

    def _set_error(self, exc: Exception) -> None:
        with self._runtime_lock:
            self._last_error = exc

    def _debug(self, format_text: str, *args: object) -> None:
        message = format_text % args if args else format_text
        LOGGER.debug(message)
        if self.debug_callback is not None:
            self.debug_callback(message)
