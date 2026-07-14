from pytest import approx

from smart_neckband.attitude import ComplementaryAttitudeFilter
from smart_neckband.buffers import ImuSample


def _imu_sample(
    *,
    timestamp_us: int,
    ax: int = 0,
    ay: int = 0,
    az: int = 16384,
    gx: int = 0,
    gy: int = 0,
    gz: int = 0,
) -> ImuSample:
    return ImuSample(
        sample_index=timestamp_us // 20_000,
        timestamp_us=timestamp_us,
        ax=ax,
        ay=ay,
        az=az,
        gx=gx,
        gy=gy,
        gz=gz,
        flags=0,
    )


def test_calibrate_flat_zeroes_current_tilt() -> None:
    attitude = ComplementaryAttitudeFilter(alpha=0.0)

    tilted = attitude.update(_imu_sample(timestamp_us=0, ax=-8192, az=14189))

    assert tilted.pitch_deg == approx(30.0, abs=0.1)

    calibrated = attitude.calibrate_flat()

    assert calibrated.roll_deg == approx(0.0)
    assert calibrated.pitch_deg == approx(0.0)
    assert calibrated.yaw_deg == approx(0.0)

    after = attitude.update(_imu_sample(timestamp_us=20_000, ax=-8192, az=14189))

    assert after.roll_deg == approx(0.0)
    assert after.pitch_deg == approx(0.0, abs=0.1)


def test_calibrate_flat_removes_stationary_yaw_gyro_bias() -> None:
    attitude = ComplementaryAttitudeFilter()

    attitude.update(_imu_sample(timestamp_us=0, gz=262))
    calibrated = attitude.calibrate_flat()
    after = attitude.update(_imu_sample(timestamp_us=1_000_000, gz=262))

    assert calibrated.yaw_deg == approx(0.0)
    assert after.yaw_deg == approx(0.0)
