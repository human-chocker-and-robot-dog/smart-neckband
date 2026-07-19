from smart_neckband.status import ConnectionSnapshot, ConnectionState, connection_state_text


def _snapshot(state: ConnectionState, **overrides: object) -> ConnectionSnapshot:
    values = {
        "state": state,
        "port": "COM19",
        "serial_open": True,
        "packet_count": 10,
        "ecg_packet_count": 5,
        "seconds_since_last_packet": 0.2,
        "measured_ecg_rate_hz": 498.5,
        "crc_errors": 0,
        "packets_lost": 0,
        "error_text": None,
    }
    values.update(overrides)
    return ConnectionSnapshot(**values)


def test_connection_state_text_receiving_in_chinese() -> None:
    text = connection_state_text(_snapshot(ConnectionState.RECEIVING))

    assert text == "正在接收 ECG：约 498 Hz"


def test_connection_state_text_reports_port_and_error() -> None:
    assert connection_state_text(_snapshot(ConnectionState.CONNECTING)) == "正在连接 COM19……"
    assert connection_state_text(
        _snapshot(ConnectionState.ERROR, error_text="串口被占用")
    ) == "连接失败：串口被占用"
