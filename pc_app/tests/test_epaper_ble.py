from __future__ import annotations

import asyncio
import time

import pytest

from smart_neckband.epaper_ble import (
    FRAME_COMPLETION_TIMEOUT_S,
    EpaperDisplayClient,
    _matches_epaper_device,
    _start_status_notify_after_bond,
)
from smart_neckband.epaper_protocol import (
    CONTROL_UUID,
    DEVICE_INFO_UUID,
    DISPLAY_SERVICE_UUID,
    FRAME_BYTES,
    FRAME_DATA_UUID,
    STATUS_UUID,
    ControlCommand,
    DeviceCapability,
    DeviceInfo,
    DisplayErrorCode,
    DisplayOwner,
    DisplayStateCode,
    DisplayStatus,
    RefreshMode,
    RefreshRequest,
    StatusFlag,
    decode_begin_frame,
    decode_frame_chunk,
    encode_device_info,
    encode_status,
    frame_crc32,
)
from smart_neckband.epaper_sync import ScheduledEpaperFrame


def _scheduled(value: int, *, force_full: bool = False) -> ScheduledEpaperFrame:
    frame = bytes([value]) * FRAME_BYTES
    return ScheduledEpaperFrame(
        frame=frame,
        crc32=frame_crc32(frame),
        source_sample_index=123,
        source_timestamp_us=456,
        force_full=force_full,
    )


def _device_info() -> DeviceInfo:
    return DeviceInfo(
        capabilities=DeviceCapability(0x3F),
        width=296,
        height=152,
        frame_bytes=FRAME_BYTES,
        max_chunk_payload=180,
        firmware_major=2,
        firmware_minor=1,
        firmware_patch=0,
        status_size=64,
        begin_frame_size=32,
        preferred_att_mtu=200,
        device_id_tail=0xB17A47E4,
        build_id=0x02010000,
    )


def _display_status(
    *,
    state: DisplayStateCode,
    active_frame_id: int = 0,
    last_frame_id: int = 0,
) -> DisplayStatus:
    return DisplayStatus(
        state=state,
        last_error=DisplayErrorCode.NONE,
        actual_refresh_mode=(
            RefreshMode.PARTIAL if state is DisplayStateCode.DONE else RefreshMode.NONE
        ),
        active_frame_id=active_frame_id,
        last_frame_id=last_frame_id,
        received_bytes=FRAME_BYTES if state is DisplayStateCode.DONE else 0,
        flags=(
            StatusFlag.LINK_ENCRYPTED
            | StatusFlag.LINK_BONDED
            | StatusFlag.LINK_CONNECTED
            | StatusFlag.SYNC_MODE
        ),
        changed_x=0,
        changed_y=80,
        changed_width=296,
        changed_height=72,
        refresh_ms=4100 if state is DisplayStateCode.DONE else 0,
        partial_refresh_count=1 if state is DisplayStateCode.DONE else 0,
        battery_percent=70,
        owner=DisplayOwner.NONE,
        milliseconds_since_last_full=1000,
        battery_mv=3900,
        pending_replaced_count=0,
        crc_error_count=0,
        timeout_count=0,
        display_failure_count=0,
        free_heap_bytes=200000,
        status_sequence=1,
    )


class FakeBleakClient:
    instances: list["FakeBleakClient"] = []

    def __init__(self, address: str, **kwargs) -> None:
        self.address = address
        self.kwargs = kwargs
        self.is_connected = True
        self.writes: list[tuple[str, bytes, bool]] = []
        self.notification = None
        self.current_frame_id = 0
        self.__class__.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        self.is_connected = False

    async def read_gatt_char(self, uuid: str) -> bytes:
        if uuid == DEVICE_INFO_UUID:
            return encode_device_info(_device_info())
        if uuid == STATUS_UUID:
            return encode_status(_display_status(state=DisplayStateCode.READY))
        raise AssertionError(f"unexpected GATT read: {uuid}")

    async def start_notify(self, uuid: str, callback) -> None:
        assert uuid == STATUS_UUID
        self.notification = callback

    async def stop_notify(self, uuid: str) -> None:
        assert uuid == STATUS_UUID

    async def write_gatt_char(self, uuid: str, data: bytes, *, response: bool) -> None:
        self.writes.append((uuid, bytes(data), response))
        if uuid == CONTROL_UUID and data[1] == ControlCommand.BEGIN_FRAME:
            self.current_frame_id = decode_begin_frame(bytes(data)).frame_id
        if uuid == CONTROL_UUID and data[1] == ControlCommand.COMMIT_FRAME:
            status = _display_status(
                state=DisplayStateCode.DONE,
                active_frame_id=0,
                last_frame_id=self.current_frame_id,
            )
            assert self.notification is not None
            self.notification(STATUS_UUID, bytearray(encode_status(status)))


class FlakyBleakClient(FakeBleakClient):
    failed_once = False

    async def write_gatt_char(self, uuid: str, data: bytes, *, response: bool) -> None:
        if uuid == FRAME_DATA_UUID and not self.__class__.failed_once:
            self.__class__.failed_once = True
            self.writes.append((uuid, bytes(data), response))
            raise RuntimeError("synthetic transient write failure")
        await super().write_gatt_char(uuid, data, response=response)


def test_epaper_scan_matches_service_or_name_only() -> None:
    assert _matches_epaper_device("Unknown", [DISPLAY_SERVICE_UUID.upper()])
    assert _matches_epaper_device("InkCanvas-Quote0-47E4", [])
    assert not _matches_epaper_device("CollarC3-2E4A", [])


def test_status_notify_retries_authentication() -> None:
    class FakeClient:
        attempts = 0

        async def start_notify(self, _uuid, _callback) -> None:
            self.attempts += 1
            if self.attempts < 3:
                raise RuntimeError("Insufficient Authentication")

    client = FakeClient()
    asyncio.run(
        _start_status_notify_after_bond(
            client,
            lambda *_args: None,
            attempts=3,
            delay_s=0,
        )
    )
    assert client.attempts == 3


def test_default_completion_timeout_covers_sync_throttle_and_panel_refresh() -> None:
    assert FRAME_COMPLETION_TIMEOUT_S >= 45.0


def test_initial_status_read_does_not_count_an_old_completed_frame() -> None:
    client = EpaperDisplayClient(address="AA:BB")
    client._handle_status_bytes(
        encode_status(
            _display_status(
                state=DisplayStateCode.DONE,
                last_frame_id=0x10203040,
            )
        ),
        count_completion=False,
    )
    assert client.runtime_status.frame_completed_count == 0
    assert client.runtime_status.display_status is not None
    assert client.runtime_status.display_status.last_frame_id == 0x10203040


def test_client_sends_latest_pending_frame_and_reconstructs_exact_bytes() -> None:
    FakeBleakClient.instances.clear()
    client = EpaperDisplayClient(
        address="AA:BB:CC:DD:EE:FF",
        client_factory=FakeBleakClient,
        completion_timeout_s=2,
    )
    first_id = client.queue_frame(_scheduled(0x11))
    second = _scheduled(0x22, force_full=True)
    second_id = client.queue_frame(second)
    assert second_id == first_id + 1
    assert client.runtime_status.local_pending_replaced_count == 1

    client.start()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if client.runtime_status.frame_completed_count == 1:
            break
        time.sleep(0.01)
    client.stop()

    assert client.runtime_status.frame_completed_count == 1
    assert client.runtime_status.display_status is not None
    assert client.runtime_status.display_status.active_frame_id == 0
    assert client.runtime_status.display_status.last_frame_id == second_id
    fake = FakeBleakClient.instances[0]
    assert fake.kwargs["pair"] is True
    assert fake.kwargs["winrt"] == {"use_cached_services": False}
    begin_packets = [
        data
        for uuid, data, _response in fake.writes
        if uuid == CONTROL_UUID and data[1] == ControlCommand.BEGIN_FRAME
    ]
    assert len(begin_packets) == 1
    begin = decode_begin_frame(begin_packets[0])
    assert begin.frame_id == second_id
    assert begin.refresh_request is RefreshRequest.FORCE_FULL
    chunks = [
        decode_frame_chunk(data)
        for uuid, data, _response in fake.writes
        if uuid == FRAME_DATA_UUID
    ]
    assert b"".join(chunk.data for chunk in chunks) == second.frame
    assert all(response for _uuid, _data, response in fake.writes)


def test_client_rejects_a_scheduled_crc_mismatch() -> None:
    client = EpaperDisplayClient(address="AA:BB")
    bad = ScheduledEpaperFrame(
        frame=b"\xFF" * FRAME_BYTES,
        crc32=0,
        source_sample_index=0,
        source_timestamp_us=0,
        force_full=False,
    )
    with pytest.raises(ValueError, match="CRC"):
        client.queue_frame(bad)


def test_client_cancels_and_retries_one_transient_transfer_failure() -> None:
    FlakyBleakClient.instances.clear()
    FlakyBleakClient.failed_once = False
    client = EpaperDisplayClient(
        address="AA:BB:CC:DD:EE:01",
        client_factory=FlakyBleakClient,
        completion_timeout_s=2,
    )
    client.queue_frame(_scheduled(0x33))
    client.start()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if client.runtime_status.frame_completed_count == 1:
            break
        time.sleep(0.01)
    client.stop()

    assert client.runtime_status.frame_completed_count == 1
    fake = FlakyBleakClient.instances[0]
    commands = [
        data[1] for uuid, data, _response in fake.writes if uuid == CONTROL_UUID
    ]
    assert commands.count(ControlCommand.BEGIN_FRAME) == 2
    assert commands.count(ControlCommand.CANCEL_FRAME) == 1
    assert commands.count(ControlCommand.COMMIT_FRAME) == 1
