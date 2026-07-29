from __future__ import annotations

import asyncio
import time

from smart_neckband.epaper_ble import (
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
    DisplayStateCode,
    DisplayStatus,
    RefreshMode,
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


class FakeBleakClient:
    instances: list["FakeBleakClient"] = []

    def __init__(self, address: str, **_kwargs) -> None:
        self.address = address
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
        assert uuid == DEVICE_INFO_UUID
        return encode_device_info(
            DeviceInfo(
                capabilities=DeviceCapability(31),
                width=296,
                height=152,
                frame_bytes=FRAME_BYTES,
                max_chunk_bytes=180,
                firmware_version="2.2.0-sync",
            )
        )

    async def start_notify(self, uuid: str, callback) -> None:
        assert uuid == STATUS_UUID
        self.notification = callback

    async def stop_notify(self, uuid: str) -> None:
        assert uuid == STATUS_UUID

    async def write_gatt_char(self, uuid: str, data: bytes, *, response: bool) -> None:
        self.writes.append((uuid, bytes(data), response))
        if uuid == CONTROL_UUID and data[0] == ControlCommand.BEGIN_FRAME:
            self.current_frame_id = decode_begin_frame(bytes(data)).frame_id
        if uuid == CONTROL_UUID and data[0] == ControlCommand.COMMIT_FRAME:
            status = DisplayStatus(
                state=DisplayStateCode.DONE,
                frame_id=self.current_frame_id,
                received_bytes=FRAME_BYTES,
                expected_bytes=FRAME_BYTES,
                refresh_mode=RefreshMode.PARTIAL,
                error_code=DisplayErrorCode.NONE,
                x=0,
                y=80,
                width=296,
                height=72,
                refresh_ms=4100,
                partial_refresh_count=1,
                battery_mv=3900,
                battery_percent=70,
                flags=StatusFlag.LINK_ENCRYPTED | StatusFlag.LINK_BONDED,
                pending_replaced_count=0,
                crc_error_count=0,
                timeout_count=0,
                display_failure_count=0,
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
    fake = FakeBleakClient.instances[0]
    begin_packets = [data for uuid, data, _response in fake.writes if uuid == CONTROL_UUID and data[0] == 1]
    assert len(begin_packets) == 1
    begin = decode_begin_frame(begin_packets[0])
    assert begin.frame_id == second_id
    assert begin.refresh_request.value == 1
    chunks = [decode_frame_chunk(data) for uuid, data, _response in fake.writes if uuid == FRAME_DATA_UUID]
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
    try:
        client.queue_frame(bad)
    except ValueError as exc:
        assert "CRC" in str(exc)
    else:
        raise AssertionError("CRC mismatch was accepted")


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
    commands = [data[0] for uuid, data, _response in fake.writes if uuid == CONTROL_UUID]
    assert commands.count(ControlCommand.BEGIN_FRAME) == 2
    assert commands.count(ControlCommand.CANCEL_FRAME) == 1
    assert commands.count(ControlCommand.COMMIT_FRAME) == 1
