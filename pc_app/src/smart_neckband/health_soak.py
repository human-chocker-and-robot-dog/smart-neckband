from __future__ import annotations

import argparse
import json
from pathlib import Path
from threading import Event, Thread
import time

from .health_mcp import HealthToolService
from .health_state import HealthStateBuilder
from .health_store import HealthStore
from .protocol import (
    FLAG_ADC_CLIPPING,
    STATUS_BT_CONNECTED,
    STATUS_MPU6050_ONLINE,
    STATUS_OLED_ONLINE,
    STATUS_SAMPLING_ACTIVE,
    DeviceStatusPayload,
    EcgPayload,
    ImuPayload,
    ImuPoint,
    PacketHeader,
    ParserStats,
)
from .serial_io import PcDataStores, SerialRuntimeStatus
from .source_coordinator import (
    EcgBatchOrdinal,
    PacketReceipt,
    utc_now_millisecond_z,
)


SOURCE_ID = "58406f6c-50cf-4f76-a659-97c6fe0fc902"


def run_soak(
    *,
    db_path: Path,
    duration_s: float,
    interval_s: float = 0.5,
) -> dict[str, object]:
    if duration_s <= 0 or interval_s <= 0:
        raise ValueError("duration_s and interval_s must be positive")
    if db_path.exists():
        raise FileExistsError(f"refusing to reuse soak database: {db_path}")

    store = HealthStore(db_path)
    stores = PcDataStores.create()
    builder = HealthStateBuilder(
        wearer_id="soak-wearer",
        data_source="synthetic",
        test_mode=True,
    )
    parser_stats = ParserStats()
    service = HealthToolService(
        store=store,
        configured_wearer_ids={"soak-wearer"},
    )
    stop_queries = Event()
    query_counts: dict[str, object] = {
        "calls": 0,
        "exceptions": 0,
        "last_exception": None,
    }

    def query_loop() -> None:
        while not stop_queries.wait(0.2):
            try:
                service.call(
                    "health.get_heart_rate",
                    {"window_s": 30},
                )
                service.call(
                    "health.get_hrv",
                    {"window_s": 30},
                )
                service.call(
                    "health.get_imu_state",
                    {"window_s": 30},
                )
                query_counts["calls"] = int(query_counts["calls"]) + 3
            except Exception as exc:
                query_counts["exceptions"] = int(
                    query_counts["exceptions"]
                ) + 1
                query_counts["last_exception"] = (
                    f"{type(exc).__name__}: {exc}"
                )[:256]

    query_thread = Thread(
        target=query_loop,
        name="HealthSoakQuery",
        daemon=True,
    )
    query_thread.start()
    started = time.monotonic()
    started_ns = time.monotonic_ns()
    iteration = 0
    packet_sequence = 0
    next_sample_ordinal = 0
    next_imu_index = 0
    last_ecg_ns: int | None = None
    last_ecg_utc: str | None = None
    last_status_second = -1
    last_status_ns: int | None = None
    last_status_utc: str | None = None
    committed_revisions = 0
    try:
        while True:
            elapsed_s = time.monotonic() - started
            if elapsed_s >= duration_s:
                break
            now_ns = time.monotonic_ns()
            now_utc = utc_now_millisecond_z()
            phase = elapsed_s % 70.0
            ecg_running = not 52.0 <= phase < 64.0
            clipping = 12.0 <= phase < 24.0
            lead_off = 44.0 <= phase < 48.0

            if ecg_running:
                sample_count = max(
                    1,
                    round(
                        500
                        * (
                            interval_s
                            if last_ecg_ns is None
                            else (now_ns - last_ecg_ns) / 1_000_000_000
                        )
                    ),
                )
                raw_first = next_sample_ordinal & 0xFFFFFFFF
                payload = EcgPayload(
                    first_sample_index=raw_first,
                    samples=(2048,) * sample_count,
                    flags=FLAG_ADC_CLIPPING if clipping else 0,
                )
                stores.ecg.append_batch(
                    PacketHeader(
                        packet_type=1,
                        payload_length=0,
                        packet_sequence=packet_sequence & 0xFFFFFFFF,
                        timestamp_us=int(elapsed_s * 1_000_000),
                    ),
                    payload,
                    source_instance_id=SOURCE_ID,
                    receipt=PacketReceipt(now_ns, now_utc),
                    ordinal=EcgBatchOrdinal(
                        raw_first_sample_index=raw_first,
                        raw_last_sample_index=(
                            raw_first + sample_count - 1
                        )
                        & 0xFFFFFFFF,
                        first_sample_ordinal=next_sample_ordinal,
                        last_sample_ordinal=(
                            next_sample_ordinal + sample_count - 1
                        ),
                    ),
                )
                next_sample_ordinal += sample_count
                packet_sequence += 1
                parser_stats.packets_ok += 1
                last_ecg_ns = now_ns
                last_ecg_utc = now_utc

            imu_count = max(1, round(50 * interval_s))
            vigorous_motion = 30.0 <= phase < 40.0
            imu_point = (
                ImuPoint(ax=16_384, ay=0, az=16_384, gx=20_000, gy=0, gz=0)
                if vigorous_motion
                else ImuPoint(ax=0, ay=0, az=16_384, gx=0, gy=0, gz=0)
            )
            stores.imu.append_batch(
                PacketHeader(
                    packet_type=2,
                    payload_length=0,
                    packet_sequence=packet_sequence & 0xFFFFFFFF,
                    timestamp_us=int(elapsed_s * 1_000_000),
                ),
                ImuPayload(
                    first_sample_index=next_imu_index,
                    samples=(imu_point,) * imu_count,
                ),
                source_instance_id=SOURCE_ID,
                receipt=PacketReceipt(now_ns, now_utc),
            )
            next_imu_index += imu_count

            elapsed_second = int(elapsed_s)
            if elapsed_second != last_status_second:
                status = DeviceStatusPayload(
                    lead_off_flags=1 if lead_off else 0,
                    sensor_status_flags=0,
                    ecg_buffer_usage_percent=1,
                    imu_buffer_usage_percent=1,
                    spp_queue_usage_percent=1,
                    status_flags=(
                        STATUS_BT_CONNECTED
                        | STATUS_MPU6050_ONLINE
                        | STATUS_OLED_ONLINE
                        | STATUS_SAMPLING_ACTIVE
                    ),
                    error_count=0,
                    ecg_ring_overflow_count=0,
                    imu_ring_overflow_count=0,
                    spp_queue_overflow_count=0,
                    transport_drop_count=0,
                    i2c_error_count=0,
                )
                stores.status.append(
                    PacketHeader(
                        packet_type=3,
                        payload_length=0,
                        packet_sequence=packet_sequence & 0xFFFFFFFF,
                        timestamp_us=int(elapsed_s * 1_000_000),
                    ),
                    status,
                    source_instance_id=SOURCE_ID,
                    receipt=PacketReceipt(now_ns, now_utc),
                )
                packet_sequence += 1
                parser_stats.packets_ok += 1
                last_status_second = elapsed_second
                last_status_ns = now_ns
                last_status_utc = now_utc

            runtime = SerialRuntimeStatus(
                port="synthetic-soak",
                started_at_monotonic_s=started_ns / 1_000_000_000,
                serial_open=True,
                packet_count=parser_stats.packets_ok,
                ecg_packet_count=max(0, iteration),
                last_packet_monotonic_s=now_ns / 1_000_000_000,
                last_ecg_sample_index=(
                    next_sample_ordinal - 1 if last_ecg_ns is not None else None
                ),
                source_instance_id=SOURCE_ID,
                last_transport_packet_monotonic_ns=now_ns,
                last_transport_packet_received_at_utc=now_utc,
                last_ecg_packet_monotonic_ns=last_ecg_ns,
                last_ecg_packet_received_at_utc=last_ecg_utc,
                last_status_packet_monotonic_ns=last_status_ns,
                last_status_packet_received_at_utc=last_status_utc,
                last_ecg_sample_ordinal=(
                    next_sample_ordinal - 1 if last_ecg_ns is not None else None
                ),
                last_ecg_raw_sample_index=(
                    (next_sample_ordinal - 1) & 0xFFFFFFFF
                    if last_ecg_ns is not None
                    else None
                ),
                last_error=None,
            )
            device = builder.build_device_snapshot(
                stores=stores,
                runtime=runtime,
                parser_stats=parser_stats,
                transport="spp",
                now_monotonic_ns=now_ns,
            )
            store.save_device_snapshot(
                wearer_id="soak-wearer",
                source_instance_id=SOURCE_ID,
                data_source="synthetic",
                device=device,
                committed_monotonic_ns=now_ns,
                transport_received_monotonic_ns=now_ns,
            )
            built = builder.build(
                stores=stores,
                runtime=runtime,
                parser_stats=parser_stats,
                analysis=None,
                transport="spp",
                now_monotonic_ns=now_ns,
            )
            if built is not None:
                committed = store.commit_state(built)
                committed_revisions = int(committed["state_revision"])
            iteration += 1
            remaining = duration_s - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(min(interval_s, remaining))
    finally:
        stop_queries.set()
        query_thread.join(5)

    outbox = store.list_outbox()
    if outbox:
        raise RuntimeError("synthetic soak created a production webhook outbox row")
    reopened = HealthStore(db_path)
    recovered_state = reopened.get_state("soak-wearer")
    if recovered_state is None:
        raise RuntimeError("soak state was not recovered after store restart")
    if reopened.list_outbox():
        raise RuntimeError("synthetic outbox appeared after store restart")
    if query_counts["exceptions"]:
        raise RuntimeError(
            "concurrent MCP query loop raised an exception: "
            f"{query_counts['last_exception']}"
        )

    events, _ = reopened.list_events(wearer_id="soak-wearer", limit=100)
    return {
        "duration_s": round(time.monotonic() - started, 3),
        "iterations": iteration,
        "state_revisions": committed_revisions,
        "events": len(events),
        "pending_outbox": len(outbox),
        "mcp_calls": int(query_counts["calls"]),
        "mcp_exceptions": int(query_counts["exceptions"]),
        "db_path": str(db_path.resolve()),
        "status": "passed",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the synthetic Health MCP stability soak",
    )
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--duration-s", type=float, default=1_800.0)
    parser.add_argument("--interval-s", type=float, default=0.5)
    parser.add_argument("--result-path", type=Path)
    args = parser.parse_args(argv)
    try:
        result = run_soak(
            db_path=args.db,
            duration_s=args.duration_s,
            interval_s=args.interval_s,
        )
    except Exception as exc:
        result = {
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc)[:512],
            "db_path": str(args.db.resolve()),
        }
    rendered = json.dumps(result, ensure_ascii=False, sort_keys=True)
    if args.result_path is not None:
        args.result_path.parent.mkdir(parents=True, exist_ok=True)
        args.result_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
