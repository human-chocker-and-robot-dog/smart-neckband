from __future__ import annotations

from dataclasses import dataclass
import math

from .buffers import ImuSample

MountingTransform = tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]

IDENTITY_MOUNTING_TRANSFORM: MountingTransform = (
    (1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, 0.0, 1.0),
)


@dataclass(frozen=True, slots=True)
class Orientation:
    roll_deg: float
    pitch_deg: float
    yaw_deg: float
    yaw_drift_note: str = "Yaw is gyro-integrated and may drift."


def _apply_transform(
    transform: MountingTransform,
    x: float,
    y: float,
    z: float,
) -> tuple[float, float, float]:
    return (
        transform[0][0] * x + transform[0][1] * y + transform[0][2] * z,
        transform[1][0] * x + transform[1][1] * y + transform[1][2] * z,
        transform[2][0] * x + transform[2][1] * y + transform[2][2] * z,
    )


class ComplementaryAttitudeFilter:
    def __init__(
        self,
        *,
        alpha: float = 0.98,
        mounting_transform: MountingTransform = IDENTITY_MOUNTING_TRANSFORM,
    ) -> None:
        self.alpha = alpha
        self.mounting_transform = mounting_transform
        self._last_timestamp_us: int | None = None
        self._roll_deg = 0.0
        self._pitch_deg = 0.0
        self._yaw_deg = 0.0
        self._roll_offset = 0.0
        self._pitch_offset = 0.0
        self._yaw_offset = 0.0

    def reset_orientation(self) -> None:
        self._roll_offset = self._roll_deg
        self._pitch_offset = self._pitch_deg
        self._yaw_offset = self._yaw_deg

    def update(self, sample: ImuSample) -> Orientation:
        ax_g, ay_g, az_g = _apply_transform(
            self.mounting_transform,
            sample.ax / 16384.0,
            sample.ay / 16384.0,
            sample.az / 16384.0,
        )
        gx_dps, gy_dps, gz_dps = _apply_transform(
            self.mounting_transform,
            sample.gx / 131.0,
            sample.gy / 131.0,
            sample.gz / 131.0,
        )

        accel_roll = math.degrees(math.atan2(ay_g, az_g))
        accel_pitch = math.degrees(math.atan2(-ax_g, math.sqrt((ay_g * ay_g) + (az_g * az_g))))

        if self._last_timestamp_us is None:
            self._roll_deg = accel_roll
            self._pitch_deg = accel_pitch
            self._yaw_deg = 0.0
        else:
            dt = max(0.0, (sample.timestamp_us - self._last_timestamp_us) / 1_000_000.0)
            gyro_roll = self._roll_deg + (gx_dps * dt)
            gyro_pitch = self._pitch_deg + (gy_dps * dt)
            self._roll_deg = (self.alpha * gyro_roll) + ((1.0 - self.alpha) * accel_roll)
            self._pitch_deg = (self.alpha * gyro_pitch) + ((1.0 - self.alpha) * accel_pitch)
            self._yaw_deg += gz_dps * dt

        self._last_timestamp_us = sample.timestamp_us
        return Orientation(
            roll_deg=self._roll_deg - self._roll_offset,
            pitch_deg=self._pitch_deg - self._pitch_offset,
            yaw_deg=self._yaw_deg - self._yaw_offset,
        )
