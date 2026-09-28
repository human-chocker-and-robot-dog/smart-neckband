import json
import re
from pathlib import Path
import pytest
from smart_neckband.acquisition_control import encode_command, decode_ack

ROOT = Path(__file__).resolve().parents[2]
VECTORS = json.loads((ROOT / "docs/protocol/acquisition_control_golden_vectors.json").read_text())["vectors"]


@pytest.mark.parametrize("vector", VECTORS)
def test_shared_control_vectors(vector):
    packet = bytes.fromhex(vector["packet_hex"])
    if vector["packet_type"] == 7:
        assert encode_command(vector["request_id"], vector["operation"]) == packet
    else:
        ack = decode_ack(packet)
        assert ack.request_id == vector["request_id"]
        assert ack.active == vector["active"]
        assert ack.result == vector["result"]
        with pytest.raises(ValueError):
            decode_ack(packet[:-1])
        corrupt = bytearray(packet)
        corrupt[22] ^= 1
        with pytest.raises(ValueError):
            decode_ack(corrupt)


def test_reject_unknown_operation():
    with pytest.raises(ValueError):
        encode_command(1, 3)


def test_firmware_self_test_uses_the_same_golden_bytes():
    source = (ROOT / "firmware/main/acquisition_control.c").read_text()
    for c_name, vector_name in (("start", "start"), ("ack", "ack_active")):
        body = re.search(r"static const uint8_t " + c_name + r"\[\] = \{(.*?)\};", source, re.S).group(1)
        encoded = bytes(int(token, 0) for token in re.findall(r"0x[0-9a-f]+|\b\d+\b", body))
        assert encoded.hex() == next(v["packet_hex"] for v in VECTORS if v["name"] == vector_name)
