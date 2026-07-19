from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ConnectionState(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED_WAITING_DATA = "CONNECTED_WAITING_DATA"
    RECEIVING = "RECEIVING"
    STALE = "STALE"
    ERROR = "ERROR"
    DISCONNECTING = "DISCONNECTING"


@dataclass(frozen=True, slots=True)
class ConnectionSnapshot:
    state: ConnectionState
    port: str | None
    serial_open: bool
    packet_count: int
    ecg_packet_count: int
    seconds_since_last_packet: float | None
    measured_ecg_rate_hz: float | None
    crc_errors: int
    packets_lost: int
    error_text: str | None = None


def connection_state_text(snapshot: ConnectionSnapshot) -> str:
    if snapshot.state is ConnectionState.DISCONNECTED:
        return "未连接"
    if snapshot.state is ConnectionState.CONNECTING:
        return f"正在连接 {snapshot.port}……" if snapshot.port else "正在连接设备……"
    if snapshot.state is ConnectionState.CONNECTED_WAITING_DATA:
        return "已连接，等待 ECG 数据"
    if snapshot.state is ConnectionState.RECEIVING:
        rate = snapshot.measured_ecg_rate_hz
        if rate is None:
            return "正在接收 ECG"
        return f"正在接收 ECG：约 {rate:.0f} Hz"
    if snapshot.state is ConnectionState.STALE:
        return "超过 2 秒未收到数据"
    if snapshot.state is ConnectionState.DISCONNECTING:
        return "正在断开连接……"
    if snapshot.state is ConnectionState.ERROR:
        if snapshot.error_text:
            return f"连接失败：{snapshot.error_text}"
        return "连接失败"
    return "未知连接状态"
