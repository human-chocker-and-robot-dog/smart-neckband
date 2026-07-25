from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import Lock

from .protocol import (
    ECG_SAMPLE_RATE_HZ,
    IMU_SAMPLE_RATE_HZ,
    DeviceStatusPayload,
    EcgPayload,
    ImuPayload,
    PacketHeader,
)
from .source_coordinator import EcgBatchOrdinal, PacketReceipt


@dataclass(frozen=True, slots=True)
class EcgSample:
    sample_index: int
    timestamp_us: int
    raw_adc: int
    flags: int
    source_instance_id: str = ""
    raw_sample_index: int | None = None
    received_monotonic_ns: int = 0
    received_at_utc: str = ""


@dataclass(frozen=True, slots=True)
class ImuSample:
    sample_index: int
    timestamp_us: int
    ax: int
    ay: int
    az: int
    gx: int
    gy: int
    gz: int
    flags: int
    source_instance_id: str = ""
    raw_sample_index: int | None = None
    received_monotonic_ns: int = 0
    received_at_utc: str = ""


@dataclass(frozen=True, slots=True)
class StatusSample:
    timestamp_us: int
    payload: DeviceStatusPayload
    source_instance_id: str = ""
    received_monotonic_ns: int = 0
    received_at_utc: str = ""


class EcgRingBuffer:
    def __init__(self, seconds: float = 30.0) -> None:
        self._samples: deque[EcgSample] = deque(maxlen=max(1, int(seconds * ECG_SAMPLE_RATE_HZ)))
        self._lock = Lock()

    def append_batch(
        self,
        header: PacketHeader,
        payload: EcgPayload,
        *,
        source_instance_id: str = "",
        receipt: PacketReceipt | None = None,
        ordinal: EcgBatchOrdinal | None = None,
    ) -> None:
        sample_period_us = int(1_000_000 / payload.sample_rate_hz)
        first_ordinal = (
            ordinal.first_sample_ordinal
            if ordinal is not None
            else payload.first_sample_index
        )
        raw_first = (
            ordinal.raw_first_sample_index
            if ordinal is not None
            else payload.first_sample_index
        )
        with self._lock:
            for offset, raw_adc in enumerate(payload.samples):
                self._samples.append(
                    EcgSample(
                        sample_index=first_ordinal + offset,
                        timestamp_us=header.timestamp_us + (offset * sample_period_us),
                        raw_adc=raw_adc,
                        flags=payload.flags,
                        source_instance_id=source_instance_id,
                        raw_sample_index=(raw_first + offset) & 0xFFFFFFFF,
                        received_monotonic_ns=(
                            receipt.received_monotonic_ns
                            if receipt is not None
                            else 0
                        ),
                        received_at_utc=(
                            receipt.received_at_utc if receipt is not None else ""
                        ),
                    )
                )

    def snapshot(self) -> tuple[EcgSample, ...]:
        with self._lock:
            return tuple(self._samples)

    def clear(self) -> None:
        with self._lock:
            self._samples.clear()

    def contains_ordinal(self, source_instance_id: str, sample_index: int) -> bool:
        with self._lock:
            return any(
                sample.source_instance_id == source_instance_id
                and sample.sample_index == sample_index
                for sample in self._samples
            )


class ImuRingBuffer:
    def __init__(self, seconds: float = 30.0) -> None:
        self._samples: deque[ImuSample] = deque(maxlen=max(1, int(seconds * IMU_SAMPLE_RATE_HZ)))
        self._lock = Lock()

    def append_batch(
        self,
        header: PacketHeader,
        payload: ImuPayload,
        *,
        source_instance_id: str = "",
        receipt: PacketReceipt | None = None,
    ) -> None:
        sample_period_us = int(1_000_000 / payload.sample_rate_hz)
        with self._lock:
            for offset, point in enumerate(payload.samples):
                self._samples.append(
                    ImuSample(
                        sample_index=payload.first_sample_index + offset,
                        timestamp_us=header.timestamp_us + (offset * sample_period_us),
                        ax=point.ax,
                        ay=point.ay,
                        az=point.az,
                        gx=point.gx,
                        gy=point.gy,
                        gz=point.gz,
                        flags=payload.flags,
                        source_instance_id=source_instance_id,
                        raw_sample_index=(payload.first_sample_index + offset)
                        & 0xFFFFFFFF,
                        received_monotonic_ns=(
                            receipt.received_monotonic_ns
                            if receipt is not None
                            else 0
                        ),
                        received_at_utc=(
                            receipt.received_at_utc if receipt is not None else ""
                        ),
                    )
                )

    def snapshot(self) -> tuple[ImuSample, ...]:
        with self._lock:
            return tuple(self._samples)

    def clear(self) -> None:
        with self._lock:
            self._samples.clear()


class StatusRingBuffer:
    def __init__(self, maxlen: int = 120) -> None:
        self._samples: deque[StatusSample] = deque(maxlen=maxlen)
        self._lock = Lock()

    def append(
        self,
        header: PacketHeader,
        payload: DeviceStatusPayload,
        *,
        source_instance_id: str = "",
        receipt: PacketReceipt | None = None,
    ) -> None:
        with self._lock:
            self._samples.append(
                StatusSample(
                    timestamp_us=header.timestamp_us,
                    payload=payload,
                    source_instance_id=source_instance_id,
                    received_monotonic_ns=(
                        receipt.received_monotonic_ns if receipt is not None else 0
                    ),
                    received_at_utc=(
                        receipt.received_at_utc if receipt is not None else ""
                    ),
                )
            )

    def latest(self) -> StatusSample | None:
        with self._lock:
            return self._samples[-1] if self._samples else None

    def snapshot(self) -> tuple[StatusSample, ...]:
        with self._lock:
            return tuple(self._samples)

    def clear(self) -> None:
        with self._lock:
            self._samples.clear()
