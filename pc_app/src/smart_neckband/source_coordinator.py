from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import time
from typing import Callable
from uuid import uuid4

from .protocol import EcgPayload, PacketType, ParsedPacket


RESET_ROLLBACK_THRESHOLD_US = 1_000_000
RESET_CONFIRMATION_TIMEOUT_NS = 2_000_000_000
RESET_MAX_BUFFERED_PACKETS = 256
RESET_MAX_BUFFERED_RAW_BYTES = 65_536
UINT32_HALF_RANGE = 0x80000000
MAX_JSON_SAFE_INTEGER = 9_007_199_254_740_991

INBOUND_PACKET_TYPES = frozenset(
    {
        PacketType.ECG_BATCH,
        PacketType.IMU_BATCH,
        PacketType.DEVICE_STATUS,
        PacketType.VOICE_TEXT_CHUNK,
        PacketType.VOICE_STATUS,
    }
)


def utc_now_millisecond_z() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


@dataclass(frozen=True, slots=True)
class PacketReceipt:
    received_monotonic_ns: int
    received_at_utc: str

    @classmethod
    def now(
        cls,
        *,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        utc_now: Callable[[], str] = utc_now_millisecond_z,
    ) -> PacketReceipt:
        return cls(
            received_monotonic_ns=monotonic_ns(),
            received_at_utc=utc_now(),
        )


@dataclass(frozen=True, slots=True)
class StagedPacket:
    packet: ParsedPacket
    receipt: PacketReceipt


@dataclass(frozen=True, slots=True)
class CoordinatorResult:
    committed: tuple[StagedPacket, ...] = ()
    dropped: tuple[StagedPacket, ...] = ()
    source_instance_id: str = ""
    rotated: bool = False


@dataclass(slots=True)
class CoordinatorStats:
    reset_candidates: int = 0
    reset_confirmed: int = 0
    reset_candidate_rejected: int = 0
    duplicate_packets: int = 0
    stale_packets: int = 0


@dataclass(slots=True)
class _PendingReset:
    trigger: StagedPacket
    old_type_max_timestamp_us: int
    started_monotonic_ns: int
    packets: list[StagedPacket]
    raw_bytes: int


def _forward_distance(new: int, old: int) -> int:
    return (new - old) & 0xFFFFFFFF


def _is_normal_forward(new: int, old: int) -> bool:
    distance = _forward_distance(new, old)
    return 1 <= distance < UINT32_HALF_RANGE


class SourceInstanceCoordinator:
    """Commit gate for decoded device-to-PC packets.

    The parser owns frame integrity. This coordinator owns source-instance
    boundaries and decides which CRC-valid decoded packets are allowed to
    affect sequence/loss statistics or downstream stores.
    """

    def __init__(
        self,
        *,
        source_instance_id_factory: Callable[[], str] = lambda: str(uuid4()),
    ) -> None:
        self._source_instance_id_factory = source_instance_id_factory
        self.source_instance_id = source_instance_id_factory()
        self.stats = CoordinatorStats()
        self._last_sequence: int | None = None
        self._max_timestamp_by_type: dict[int, int] = {}
        self._pending: _PendingReset | None = None

    @property
    def pending(self) -> bool:
        return self._pending is not None

    def start_new_reader(self) -> str:
        self.source_instance_id = self._source_instance_id_factory()
        self._last_sequence = None
        self._max_timestamp_by_type.clear()
        self._pending = None
        return self.source_instance_id

    def ingest(self, staged: StagedPacket) -> CoordinatorResult:
        packet_type = PacketType(staged.packet.header.packet_type)
        if packet_type not in INBOUND_PACKET_TYPES:
            self.stats.stale_packets += 1
            return self._result(dropped=(staged,))

        if self._pending is not None:
            return self._ingest_pending(staged)
        return self._ingest_normal(staged, allow_candidate=True)

    def flush_expired(self, now_monotonic_ns: int) -> CoordinatorResult:
        pending = self._pending
        if pending is None:
            return self._result()
        if now_monotonic_ns - pending.started_monotonic_ns <= RESET_CONFIRMATION_TIMEOUT_NS:
            return self._result()
        return self._reject_pending()

    def _ingest_normal(
        self,
        staged: StagedPacket,
        *,
        allow_candidate: bool,
    ) -> CoordinatorResult:
        header = staged.packet.header
        packet_type = int(header.packet_type)
        type_max = self._max_timestamp_by_type.get(packet_type)
        sequence_forward = (
            self._last_sequence is None
            or _is_normal_forward(header.packet_sequence, self._last_sequence)
        )
        timestamp_rolled_back = (
            type_max is not None
            and type_max - header.timestamp_us > RESET_ROLLBACK_THRESHOLD_US
        )

        if (
            allow_candidate
            and self._last_sequence is not None
            and not sequence_forward
            and timestamp_rolled_back
        ):
            self.stats.reset_candidates += 1
            self._pending = _PendingReset(
                trigger=staged,
                old_type_max_timestamp_us=type_max,
                started_monotonic_ns=staged.receipt.received_monotonic_ns,
                packets=[staged],
                raw_bytes=len(staged.packet.raw),
            )
            return self._result()

        if self._last_sequence is not None and not sequence_forward:
            if header.packet_sequence == self._last_sequence:
                self.stats.duplicate_packets += 1
            else:
                self.stats.stale_packets += 1
            return self._result(dropped=(staged,))

        self._accept_baseline(staged)
        return self._result(committed=(staged,))

    def _ingest_pending(self, staged: StagedPacket) -> CoordinatorResult:
        pending = self._pending
        assert pending is not None
        pending.packets.append(staged)
        pending.raw_bytes += len(staged.packet.raw)

        if (
            len(pending.packets) > RESET_MAX_BUFFERED_PACKETS
            or pending.raw_bytes > RESET_MAX_BUFFERED_RAW_BYTES
            or staged.receipt.received_monotonic_ns - pending.started_monotonic_ns
            > RESET_CONFIRMATION_TIMEOUT_NS
        ):
            return self._reject_pending()

        trigger_header = pending.trigger.packet.header
        header = staged.packet.header
        confirmed = (
            header.packet_type == trigger_header.packet_type
            and _is_normal_forward(
                header.packet_sequence,
                trigger_header.packet_sequence,
            )
            and header.timestamp_us >= trigger_header.timestamp_us
            and pending.old_type_max_timestamp_us - header.timestamp_us
            > RESET_ROLLBACK_THRESHOLD_US
        )
        if not confirmed:
            return self._result()

        replay = tuple(pending.packets)
        self._pending = None
        self.source_instance_id = self._source_instance_id_factory()
        self._last_sequence = None
        self._max_timestamp_by_type.clear()
        self.stats.reset_confirmed += 1
        for packet in replay:
            self._accept_baseline(packet)
        return self._result(committed=replay, rotated=True)

    def _reject_pending(self) -> CoordinatorResult:
        pending = self._pending
        assert pending is not None
        self._pending = None
        self.stats.reset_candidate_rejected += 1
        committed: list[StagedPacket] = []
        dropped: list[StagedPacket] = [pending.trigger]
        for staged in pending.packets[1:]:
            result = self._ingest_normal(staged, allow_candidate=False)
            committed.extend(result.committed)
            dropped.extend(result.dropped)
        return self._result(
            committed=tuple(committed),
            dropped=tuple(dropped),
        )

    def _accept_baseline(self, staged: StagedPacket) -> None:
        header = staged.packet.header
        self._last_sequence = header.packet_sequence
        packet_type = int(header.packet_type)
        previous = self._max_timestamp_by_type.get(packet_type)
        if previous is None or header.timestamp_us > previous:
            self._max_timestamp_by_type[packet_type] = header.timestamp_us

    def _result(
        self,
        *,
        committed: tuple[StagedPacket, ...] = (),
        dropped: tuple[StagedPacket, ...] = (),
        rotated: bool = False,
    ) -> CoordinatorResult:
        return CoordinatorResult(
            committed=committed,
            dropped=dropped,
            source_instance_id=self.source_instance_id,
            rotated=rotated,
        )


@dataclass(frozen=True, slots=True)
class EcgBatchOrdinal:
    raw_first_sample_index: int
    raw_last_sample_index: int
    first_sample_ordinal: int
    last_sample_ordinal: int


class EcgSampleOrdinalExtender:
    def __init__(self) -> None:
        self._last_raw_sample_index: int | None = None
        self._last_sample_ordinal: int | None = None

    def reset(self) -> None:
        self._last_raw_sample_index = None
        self._last_sample_ordinal = None

    def extend(self, payload: EcgPayload) -> EcgBatchOrdinal:
        if not payload.samples:
            raise ValueError("ECG batch must contain at least one sample")
        raw_first = payload.first_sample_index & 0xFFFFFFFF
        if self._last_raw_sample_index is None:
            first_ordinal = raw_first
        else:
            distance = _forward_distance(raw_first, self._last_raw_sample_index)
            if distance == 0:
                raise ValueError("duplicate ECG sample index")
            if distance >= UINT32_HALF_RANGE:
                raise ValueError("stale ECG sample index")
            assert self._last_sample_ordinal is not None
            first_ordinal = self._last_sample_ordinal + distance

        last_ordinal = first_ordinal + len(payload.samples) - 1
        if last_ordinal > MAX_JSON_SAFE_INTEGER:
            raise OverflowError("ECG sample ordinal exceeds JSON safe integer")
        raw_last = (raw_first + len(payload.samples) - 1) & 0xFFFFFFFF
        self._last_raw_sample_index = raw_last
        self._last_sample_ordinal = last_ordinal
        return EcgBatchOrdinal(
            raw_first_sample_index=raw_first,
            raw_last_sample_index=raw_last,
            first_sample_ordinal=first_ordinal,
            last_sample_ordinal=last_ordinal,
        )
