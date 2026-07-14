from types import SimpleNamespace

from serial.tools import list_ports

from smart_neckband.serial_io import list_serial_ports


def test_list_serial_ports_marks_bluetooth_outgoing_port(monkeypatch) -> None:
    monkeypatch.setattr(
        list_ports,
        "comports",
        lambda: [
            SimpleNamespace(
                device="COM19",
                description="Bluetooth serial",
                hwid=r"BTHENUM\{00001101}_LOCALMFG&001D\0&08B61F3D3386_C00000000",
            ),
            SimpleNamespace(
                device="COM20",
                description="Bluetooth serial",
                hwid=r"BTHENUM\{00001101}_LOCALMFG&0000\0&000000000000_00000002",
            ),
        ],
    )

    ports = list_serial_ports()

    assert ports[0].is_bluetooth_candidate is True
    assert ports[0].is_bluetooth_outgoing is True
    assert ports[1].is_bluetooth_candidate is True
    assert ports[1].is_bluetooth_outgoing is False
