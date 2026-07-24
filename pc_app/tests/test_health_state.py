from __future__ import annotations

from dataclasses import replace

import jsonschema
import pytest

from smart_neckband.analysis import EcgAnalysisResult
from smart_neckband.health_contract import definition_schema
from smart_neckband.health_quality import HealthQualityWindow
from smart_neckband.health_state import HealthStateBuilder
from smart_neckband.protocol import (
    STATUS_BT_CONNECTED,
    STATUS_MPU6050_ONLINE,
    STATUS_OLED_ONLINE,
    STATUS_SAMPLING_ACTIVE,
    FLAG_ADC_CLIPPING,
    DeviceStatusPayload,
    EcgPayload,
    PacketHeader,
    ParserStats,
)
from smart_neckband.serial_io import PcDataStores, SerialRuntimeStatus
from smart_neckband.source_coordinator import EcgBatchOrdinal, PacketReceipt


SOURCE_ID = "ef132c67-a98f-474a-a673-4ab6ea784790"


@pytest.mark.parametrize(
    "wearer_id",
    ("", " contains-space", "a" * 65, "slash/not-allowed"),
)
def test_builder_rejects_invalid_wearer_id(wearer_id) -> None:
    with pytest.raises(ValueError, match="wearer_id must match"):
        HealthStateBuilder(wearer_id=wearer_id)


def runtime(
    *,
    ecg_ns: int | None,
    transport_ns: int | None,
    ordinal: int | None,
    raw_index: int | None,
    serial_open: bool = True,
) -> SerialRuntimeStatus:
    return SerialRuntimeStatus(
        port="COM19",
        started_at_monotonic_s=0.0,
        serial_open=serial_open,
        packet_count=2,
        ecg_packet_count=1,
        last_packet_monotonic_s=(
            transport_ns / 1_000_000_000 if transport_ns is not None else None
        ),
        last_ecg_sample_index=ordinal,
        source_instance_id=SOURCE_ID,
        last_transport_packet_monotonic_ns=transport_ns,
        last_transport_packet_received_at_utc=(
            "2026-07-24T00:00:00.100Z" if transport_ns is not None else None
        ),
        last_ecg_packet_monotonic_ns=ecg_ns,
        last_ecg_packet_received_at_utc=(
            "2026-07-24T00:00:00.000Z" if ecg_ns is not None else None
        ),
        last_status_packet_monotonic_ns=transport_ns,
        last_status_packet_received_at_utc=(
            "2026-07-24T00:00:00.100Z" if transport_ns is not None else None
        ),
        last_ecg_sample_ordinal=ordinal,
        last_ecg_raw_sample_index=raw_index,
        last_error=None,
    )


def populated_stores(
    *,
    ecg_ns: int = 1_000_000_000,
    status_ns: int = 1_100_000_000,
    lead_off: bool = False,
    sample_count_batches: int = 1,
    ecg_flags: int = 0,
    counter_value: int = 0,
    source_id: str = SOURCE_ID,
) -> PcDataStores:
    stores = PcDataStores.create()
    for batch in range(sample_count_batches):
        first = batch * 20
        payload = EcgPayload(
            first_sample_index=first,
            samples=(2048,) * 20,
            flags=ecg_flags,
        )
        stores.ecg.append_batch(
            PacketHeader(
                packet_type=1,
                payload_length=0,
                packet_sequence=batch,
                timestamp_us=batch * 40_000,
            ),
            payload,
            source_instance_id=source_id,
            receipt=PacketReceipt(
                received_monotonic_ns=ecg_ns + batch * 40_000_000,
                received_at_utc="2026-07-24T00:00:00.000Z",
            ),
            ordinal=EcgBatchOrdinal(
                raw_first_sample_index=first,
                raw_last_sample_index=first + 19,
                first_sample_ordinal=first,
                last_sample_ordinal=first + 19,
            ),
        )
    flags = (
        STATUS_BT_CONNECTED
        | STATUS_MPU6050_ONLINE
        | STATUS_OLED_ONLINE
        | STATUS_SAMPLING_ACTIVE
    )
    stores.status.append(
        PacketHeader(
            packet_type=3,
            payload_length=0,
            packet_sequence=1000,
            timestamp_us=1_000_000,
        ),
        DeviceStatusPayload(
            lead_off_flags=1 if lead_off else 0,
            sensor_status_flags=0,
            ecg_buffer_usage_percent=1,
            imu_buffer_usage_percent=1,
            spp_queue_usage_percent=1,
            status_flags=flags,
            error_count=counter_value,
            ecg_ring_overflow_count=counter_value,
            imu_ring_overflow_count=counter_value,
            spp_queue_overflow_count=counter_value,
            transport_drop_count=counter_value,
            i2c_error_count=counter_value,
        ),
        source_instance_id=source_id,
        receipt=PacketReceipt(
            received_monotonic_ns=status_ns,
            received_at_utc="2026-07-24T00:00:00.100Z",
        ),
    )
    return stores


def test_builder_does_not_create_wearer_state_before_first_ecg() -> None:
    builder = HealthStateBuilder(wearer_id="xwen")
    stores = PcDataStores.create()

    result = builder.build(
        stores=stores,
        runtime=runtime(
            ecg_ns=None,
            transport_ns=1_000_000_000,
            ordinal=None,
            raw_index=None,
        ),
        parser_stats=ParserStats(),
        analysis=None,
        transport="spp",
        now_monotonic_ns=1_100_000_000,
    )

    assert result is None


def test_non_ecg_transport_keeps_device_degraded_but_health_offline() -> None:
    builder = HealthStateBuilder(wearer_id="xwen")
    stores = populated_stores()

    built = builder.build(
        stores=stores,
        runtime=runtime(
            ecg_ns=1_000_000_000,
            transport_ns=12_500_000_000,
            ordinal=19,
            raw_index=19,
        ),
        parser_stats=ParserStats(packets_ok=10),
        analysis=None,
        transport="spp",
        now_monotonic_ns=12_600_000_000,
    )

    assert built is not None
    assert built.document["age_ms"] == 11_600
    assert built.document["freshness"] == "offline"
    assert built.document["device"]["last_transport_packet_age_ms"] == 100
    assert built.document["device"]["status"] == "degraded"


def test_reader_disconnect_immediately_forces_health_offline() -> None:
    builder = HealthStateBuilder(wearer_id="xwen")
    stores = populated_stores()

    built = builder.build(
        stores=stores,
        runtime=runtime(
            ecg_ns=1_000_000_000,
            transport_ns=1_100_000_000,
            ordinal=19,
            raw_index=19,
            serial_open=False,
        ),
        parser_stats=ParserStats(packets_ok=2),
        analysis=None,
        transport="spp",
        now_monotonic_ns=1_200_000_000,
    )

    assert built is not None
    assert built.document["age_ms"] == 200
    assert built.document["freshness"] == "offline"
    assert built.document["device"]["status"] == "offline"
    assert built.document["device"]["transport_connected"] is False


def test_quality_uses_ten_second_parser_counter_deltas() -> None:
    builder = HealthStateBuilder(wearer_id="xwen")
    stores = populated_stores()
    initial = builder.build(
        stores=stores,
        runtime=runtime(
            ecg_ns=1_000_000_000,
            transport_ns=1_100_000_000,
            ordinal=19,
            raw_index=19,
        ),
        parser_stats=ParserStats(
            packets_ok=1_000,
            packets_lost=100,
            crc_errors=50,
        ),
        analysis=None,
        transport="spp",
        now_monotonic_ns=1_200_000_000,
    )
    current = builder.build(
        stores=stores,
        runtime=runtime(
            ecg_ns=1_000_000_000,
            transport_ns=1_100_000_000,
            ordinal=19,
            raw_index=19,
        ),
        parser_stats=ParserStats(
            packets_ok=1_100,
            packets_lost=101,
            crc_errors=50,
        ),
        analysis=None,
        transport="spp",
        now_monotonic_ns=2_200_000_000,
    )

    assert initial is not None
    assert current is not None
    assert current.document["signal"]["packet_loss_ratio_10s"] == 1 / 101
    assert current.document["signal"]["crc_errors_delta_10s"] == 0


def test_quality_counter_window_resets_on_source_rotation() -> None:
    window = HealthQualityWindow()
    first = window.observe(
        source_instance_id="source-a",
        now_monotonic_ns=1_000_000_000,
        parser_stats=ParserStats(
            packets_ok=100,
            packets_lost=10,
            crc_errors=5,
        ),
        latest_status=None,
    )
    second = window.observe(
        source_instance_id="source-a",
        now_monotonic_ns=2_000_000_000,
        parser_stats=ParserStats(
            packets_ok=200,
            packets_lost=11,
            crc_errors=6,
        ),
        latest_status=None,
    )
    rotated = window.observe(
        source_instance_id="source-b",
        now_monotonic_ns=3_000_000_000,
        parser_stats=ParserStats(
            packets_ok=200,
            packets_lost=11,
            crc_errors=6,
        ),
        latest_status=None,
    )

    assert first.packet_loss_ratio_10s is None
    assert second.packet_loss_ratio_10s == 1 / 101
    assert second.crc_errors_delta_10s == 1
    assert rotated.packet_loss_ratio_10s is None
    assert rotated.crc_errors_delta_10s == 0


def test_clipping_requires_full_receive_time_window_and_is_source_scoped() -> None:
    builder = HealthStateBuilder(wearer_id="xwen")
    stores = populated_stores(
        sample_count_batches=250,
        ecg_flags=FLAG_ADC_CLIPPING,
        status_ns=10_960_000_000,
    )
    latest = stores.ecg.snapshot()[-1]
    partial = builder.build(
        stores=stores,
        runtime=runtime(
            ecg_ns=latest.received_monotonic_ns,
            transport_ns=latest.received_monotonic_ns,
            ordinal=latest.sample_index,
            raw_index=latest.raw_sample_index,
        ),
        parser_stats=ParserStats(packets_ok=250),
        analysis=None,
        transport="spp",
        now_monotonic_ns=latest.received_monotonic_ns + 10_000_000,
    )

    assert partial is not None
    assert partial.document["signal"]["adc_clipping_ratio_10s"] == 1.0
    assert partial.clipping_window_full is False

    stores.ecg.append_batch(
        PacketHeader(
            packet_type=1,
            payload_length=0,
            packet_sequence=250,
            timestamp_us=10_000_000,
        ),
        EcgPayload(
            first_sample_index=5_000,
            samples=(2048,) * 20,
            flags=FLAG_ADC_CLIPPING,
        ),
        source_instance_id=SOURCE_ID,
        receipt=PacketReceipt(
            received_monotonic_ns=11_000_000_000,
            received_at_utc="2026-07-24T00:00:10.000Z",
        ),
        ordinal=EcgBatchOrdinal(
            raw_first_sample_index=5_000,
            raw_last_sample_index=5_019,
            first_sample_ordinal=5_000,
            last_sample_ordinal=5_019,
        ),
    )
    full = builder.build(
        stores=stores,
        runtime=runtime(
            ecg_ns=11_000_000_000,
            transport_ns=11_000_000_000,
            ordinal=5_019,
            raw_index=5_019,
        ),
        parser_stats=ParserStats(packets_ok=251),
        analysis=None,
        transport="spp",
        now_monotonic_ns=11_010_000_000,
    )

    assert full is not None
    assert full.clipping_window_full is True


def test_device_counter_degrade_uses_delta_not_historical_total() -> None:
    builder = HealthStateBuilder(wearer_id="xwen")
    stores = populated_stores(counter_value=7)
    stats = ParserStats(packets_ok=2)
    first = builder.build(
        stores=stores,
        runtime=runtime(
            ecg_ns=1_000_000_000,
            transport_ns=1_100_000_000,
            ordinal=19,
            raw_index=19,
        ),
        parser_stats=stats,
        analysis=None,
        transport="spp",
        now_monotonic_ns=1_200_000_000,
    )
    assert first is not None
    assert first.document["device"]["status"] == "ok"

    previous_status = stores.status.latest()
    assert previous_status is not None
    stores.status.append(
        PacketHeader(
            packet_type=3,
            payload_length=0,
            packet_sequence=1001,
            timestamp_us=2_000_000,
        ),
        previous_status.payload,
        source_instance_id=SOURCE_ID,
        receipt=PacketReceipt(
            received_monotonic_ns=2_000_000_000,
            received_at_utc="2026-07-24T00:00:01.000Z",
        ),
    )
    unchanged = builder.build(
        stores=stores,
        runtime=runtime(
            ecg_ns=1_000_000_000,
            transport_ns=2_000_000_000,
            ordinal=19,
            raw_index=19,
        ),
        parser_stats=stats,
        analysis=None,
        transport="spp",
        now_monotonic_ns=2_100_000_000,
    )
    assert unchanged is not None
    assert unchanged.document["device"]["status"] == "ok"

    stores.status.append(
        PacketHeader(
            packet_type=3,
            payload_length=0,
            packet_sequence=1002,
            timestamp_us=3_000_000,
        ),
        replace(previous_status.payload, error_count=8),
        source_instance_id=SOURCE_ID,
        receipt=PacketReceipt(
            received_monotonic_ns=3_000_000_000,
            received_at_utc="2026-07-24T00:00:02.000Z",
        ),
    )
    increased = builder.build(
        stores=stores,
        runtime=runtime(
            ecg_ns=1_000_000_000,
            transport_ns=3_000_000_000,
            ordinal=19,
            raw_index=19,
        ),
        parser_stats=stats,
        analysis=None,
        transport="spp",
        now_monotonic_ns=3_100_000_000,
    )
    assert increased is not None
    assert increased.document["device"]["status"] == "degraded"


def test_stalled_analysis_invalidates_metrics_while_ecg_remains_fresh() -> None:
    builder = HealthStateBuilder(wearer_id="xwen")
    stores = populated_stores(ecg_ns=4_000_000_000, status_ns=4_100_000_000)
    analysis = EcgAnalysisResult(
        timestamp_s=0.0,
        raw=(0.0,),
        cleaned=(0.0,),
        r_peak_indices=(1, 2),
        heart_rate_bpm=60.0,
        latest_rr_ms=1_000.0,
        signal_quality=0.9,
        message="ok",
        source_instance_id=SOURCE_ID,
        analyzed_through_ecg_sample_index=19,
        analyzed_through_received_monotonic_ns=1_000_000_000,
        analyzed_through_received_at_utc="2026-07-24T00:00:00.000Z",
    )

    built = builder.build(
        stores=stores,
        runtime=runtime(
            ecg_ns=4_000_000_000,
            transport_ns=4_100_000_000,
            ordinal=19,
            raw_index=19,
        ),
        parser_stats=ParserStats(packets_ok=10),
        analysis=analysis,
        transport="spp",
        now_monotonic_ns=4_200_000_000,
    )

    assert built is not None
    assert built.document["freshness"] == "fresh"
    assert built.document["heart"]["heart_rate"]["valid"] is False
    assert (
        built.document["heart"]["heart_rate"]["unavailable_reason"]
        == "stale_data"
    )


def test_fresh_state_validates_against_machine_contract() -> None:
    builder = HealthStateBuilder(
        wearer_id="xwen",
        data_source="synthetic",
        test_mode=True,
    )
    stores = populated_stores(
        ecg_ns=1_000_000_000,
        status_ns=10_960_000_000,
        sample_count_batches=250,
    )
    latest = stores.ecg.snapshot()[-1]
    analysis = EcgAnalysisResult(
        timestamp_s=0.0,
        raw=(2048.0,) * 5_000,
        cleaned=(0.0,) * 5_000,
        r_peak_indices=(100, 600),
        heart_rate_bpm=60.0,
        latest_rr_ms=1_000.0,
        signal_quality=0.9,
        message="ok",
        source_instance_id=SOURCE_ID,
        analyzed_through_ecg_sample_index=latest.sample_index,
        analyzed_through_received_monotonic_ns=latest.received_monotonic_ns,
        analyzed_through_received_at_utc=latest.received_at_utc,
    )
    now_ns = latest.received_monotonic_ns + 100_000_000
    built = builder.build(
        stores=stores,
        runtime=runtime(
            ecg_ns=latest.received_monotonic_ns,
            transport_ns=latest.received_monotonic_ns,
            ordinal=latest.sample_index,
            raw_index=latest.raw_sample_index,
        ),
        parser_stats=ParserStats(packets_ok=250),
        analysis=analysis,
        transport="spp",
        now_monotonic_ns=now_ns,
    )

    assert built is not None
    built.document["state_revision"] = 1
    jsonschema.Draft202012Validator(definition_schema("WearerState")).validate(
        built.document
    )
    assert built.document["heart"]["heart_rate"]["valid"] is True
