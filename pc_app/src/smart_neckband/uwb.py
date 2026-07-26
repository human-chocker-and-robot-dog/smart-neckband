from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class DistanceReading:
    distance_m: float | None
    quality: float | None
    source: str


class DistanceProvider(Protocol):
    def latest(self) -> DistanceReading:
        """Return the latest human-to-dog distance reading."""


class UnavailableDistanceProvider:
    def latest(self) -> DistanceReading:
        return DistanceReading(distance_m=None, quality=None, source="unavailable")
