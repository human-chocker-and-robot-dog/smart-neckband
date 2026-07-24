from __future__ import annotations

from smart_neckband.protocol import (
    DeviceStatusPayload,
    ImuPoint,
    VoiceTextChunkPayload,
    decode_packet,
    encode_device_status_packet,
    encode_ecg_packet,
    encode_imu_packet,
    encode_voice_text_chunk_packet,
)
from smart_neckband.source_coordinator import (
    EcgSampleOrdinalExtender,
    PacketReceipt,
    SourceInstanceCoordinator,
    StagedPacket,
)


def receipt(monotonic_ns: int) -> PacketReceipt:
    return PacketReceipt(
        received_monotonic_ns=monotonic_ns,
        received_at_utc="2026-07-24T00:00:00.000Z",
    )


def staged(raw: bytes, monotonic_ns: int) -> StagedPacket:
    return StagedPacket(packet=decode_packet(raw), receipt=receipt(monotonic_ns))


def ecg(sequence: int, timestamp_us: int, first: int = 0) -> bytes:
    return encode_ecg_packet(
        packet_sequence=sequence,
        timestamp_us=timestamp_us,
        first_sample_index=first,
        samples=(2048,) * 20,
    )


def imu(sequence: int, timestamp_us: int, first: int = 0) -> bytes:
    point = ImuPoint(ax=1, ay=2, az=3, gx=4, gy=5, gz=6)
    return encode_imu_packet(
        packet_sequence=sequence,
        timestamp_us=timestamp_us,
        first_sample_index=first,
        samples=(point, point),
    )


def status(sequence: int, timestamp_us: int) -> bytes:
    return encode_device_status_packet(
        packet_sequence=sequence,
        timestamp_us=timestamp_us,
        status=DeviceStatusPayload(
            lead_off_flags=0,
            sensor_status_flags=0,
            ecg_buffer_usage_percent=0,
            imu_buffer_usage_percent=0,
            spp_queue_usage_percent=0,
            status_flags=0,
            error_count=0,
            ecg_ring_overflow_count=0,
            imu_ring_overflow_count=0,
            spp_queue_overflow_count=0,
            transport_drop_count=0,
            i2c_error_count=0,
        ),
    )


def voice(
    sequence: int,
    timestamp_us: int,
    *,
    utterance_id: int,
    chunk_index: int = 0,
    chunk_count: int = 1,
) -> bytes:
    return encode_voice_text_chunk_packet(
        packet_sequence=sequence,
        timestamp_us=timestamp_us,
        chunk=VoiceTextChunkPayload(
            utterance_id=utterance_id,
            chunk_index=chunk_index,
            chunk_count=chunk_count,
            text_bytes=b"x",
        ),
    )


def test_timestamp_rollback_threshold_is_exclusive() -> None:
    ids = iter(("source-a", "source-b"))
    coordinator = SourceInstanceCoordinator(
        source_instance_id_factory=lambda: next(ids)
    )
    assert coordinator.ingest(staged(ecg(100, 5_000_000), 1)).committed

    exactly = coordinator.ingest(staged(ecg(10, 4_000_000), 2))

    assert exactly.dropped
    assert not coordinator.pending
    assert coordinator.stats.reset_candidates == 0


def test_reset_buffers_interleaved_packets_and_rotates_once() -> None:
    ids = iter(("source-a", "source-b", "source-c"))
    coordinator = SourceInstanceCoordinator(
        source_instance_id_factory=lambda: next(ids)
    )
    assert coordinator.ingest(staged(ecg(100, 5_000_001), 1)).committed

    assert not coordinator.ingest(staged(ecg(1, 1_000_000), 2)).committed
    assert not coordinator.ingest(
        staged(voice(2, 1_050_000, utterance_id=9), 3)
    ).committed
    assert not coordinator.ingest(staged(imu(3, 1_100_000), 4)).committed
    assert not coordinator.ingest(staged(status(4, 1_150_000), 5)).committed
    confirmed = coordinator.ingest(staged(ecg(5, 1_200_000, first=20), 6))

    assert confirmed.rotated
    assert confirmed.source_instance_id == "source-b"
    assert [item.packet.header.packet_sequence for item in confirmed.committed] == [
        1,
        2,
        3,
        4,
        5,
    ]
    assert coordinator.stats.reset_confirmed == 1
    assert not coordinator.pending

    following = coordinator.ingest(staged(status(6, 1_250_000), 7))
    assert not following.rotated
    assert coordinator.source_instance_id == "source-b"


def test_voice_packets_can_trigger_and_confirm_real_reset() -> None:
    ids = iter(("source-a", "source-b"))
    coordinator = SourceInstanceCoordinator(
        source_instance_id_factory=lambda: next(ids)
    )
    assert coordinator.ingest(
        staged(voice(100, 5_000_001, utterance_id=1), 1)
    ).committed
    assert not coordinator.ingest(
        staged(
            voice(
                1,
                1_000_000,
                utterance_id=2,
                chunk_index=0,
                chunk_count=2,
            ),
            2,
        )
    ).committed

    result = coordinator.ingest(
        staged(
            voice(
                2,
                1_100_000,
                utterance_id=2,
                chunk_index=1,
                chunk_count=2,
            ),
            3,
        )
    )

    assert result.rotated
    assert len(result.committed) == 2
    assert coordinator.stats.reset_confirmed == 1


def test_pending_capacity_rejects_trigger_without_rotation() -> None:
    ids = iter(("source-a", "source-b"))
    coordinator = SourceInstanceCoordinator(
        source_instance_id_factory=lambda: next(ids)
    )
    assert coordinator.ingest(staged(ecg(500, 5_000_001), 1)).committed
    assert not coordinator.ingest(staged(ecg(1, 1_000_000), 2)).committed

    final = None
    for index in range(2, 258):
        final = coordinator.ingest(
            staged(imu(index, 1_000_000 + index, first=index * 2), index + 1)
        )

    assert final is not None
    assert not final.rotated
    assert coordinator.source_instance_id == "source-a"
    assert coordinator.stats.reset_candidate_rejected == 1
    assert not coordinator.pending


def test_ecg_sample_ordinal_extends_across_uint32_wrap() -> None:
    extender = EcgSampleOrdinalExtender()
    first = decode_packet(ecg(1, 1, first=0xFFFFFFF0)).payload
    second = decode_packet(ecg(2, 2, first=4)).payload

    first_result = extender.extend(first)
    second_result = extender.extend(second)

    assert first_result.first_sample_ordinal == 4_294_967_280
    assert first_result.last_sample_ordinal == 4_294_967_299
    assert second_result.first_sample_ordinal == 4_294_967_300
    assert second_result.last_sample_ordinal == 4_294_967_319
    assert second_result.raw_last_sample_index == 23
