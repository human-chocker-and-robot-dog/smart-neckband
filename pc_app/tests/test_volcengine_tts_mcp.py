from __future__ import annotations

from array import array
import asyncio
import base64
import importlib.util
import json
from pathlib import Path
from queue import Empty
import sys


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "tools" / "volcengine_tts_mcp.py"
)
SPEC = importlib.util.spec_from_file_location("volcengine_tts_mcp", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def settings(**overrides):
    values = {
        "app_id": "app",
        "access_token": "token",
        "resource_id": "seed-tts-2.0",
        "voice": "speaker",
        "uid": "test-user",
    }
    values.update(overrides)
    result = MODULE.Settings(**values)
    result.validate()
    return result


def test_concatenated_json_parser_handles_split_utf8_and_multiple_frames() -> None:
    first = json.dumps(
        {"code": 0, "data": base64.b64encode(b"\x01\x00").decode()},
        ensure_ascii=False,
    )
    second = json.dumps(
        {"code": MODULE.COMPLETION_CODE, "message": "完成"},
        ensure_ascii=False,
    )
    raw = (first + second).encode("utf-8")
    marker = raw.index("完成".encode("utf-8")) + 1

    frames = list(
        MODULE.iter_concatenated_json(
            [raw[:7], raw[7:marker], raw[marker : marker + 1], raw[marker + 1 :]]
        )
    )

    assert [frame["code"] for frame in frames] == [0, MODULE.COMPLETION_CODE]
    assert frames[1]["message"] == "完成"


def test_pcm_volume_scaling_is_silent_at_zero_and_linear_at_half() -> None:
    samples = array("h", [-20_000, -1_000, 0, 1_000, 20_000])
    pcm = samples.tobytes()

    assert MODULE.scale_pcm_s16le(pcm, 0) == bytes(len(pcm))
    assert MODULE.scale_pcm_s16le(pcm, 100) == pcm

    half = array("h")
    half.frombytes(MODULE.scale_pcm_s16le(pcm, 50))
    assert half.tolist() == [-10_000, -500, 0, 500, 10_000]


def test_request_payload_uses_v3_pcm_audio_parameters() -> None:
    client = MODULE.VolcengineTtsClient(
        settings(sample_rate=24_000, pitch_ratio=2, loudness_ratio=-1)
    )

    payload = client.request_payload("你好", -20)

    assert payload == {
        "user": {"uid": "test-user"},
        "req_params": {
            "text": "你好",
            "speaker": "speaker",
            "audio_params": {
                "format": "pcm",
                "sample_rate": 24_000,
                "speech_rate": -20,
                "pitch_ratio": 2,
                "loudness_ratio": -1,
            },
        },
    }


def test_enqueue_returns_acceptance_without_running_network_or_audio() -> None:
    service = MODULE.SpeechService(settings(default_volume=65, queue_size=2))

    result = service.enqueue({"text": "立即返回", "volume": 40})

    assert result["accepted"] is True
    assert result["queue_depth"] == 1
    assert result["volume"] == 40
    job = service.queue.get_nowait()
    assert job.text == "立即返回"
    assert job.volume == 40
    service.queue.task_done()
    with __import__("pytest").raises(Empty):
        service.queue.get_nowait()


def test_enqueue_rejects_invalid_and_full_requests() -> None:
    service = MODULE.SpeechService(settings(default_volume=65, queue_size=1))

    assert service.enqueue({"text": ""})["accepted"] is False
    assert service.enqueue({"text": "one"})["accepted"] is True
    full = service.enqueue({"text": "two"})

    assert full["accepted"] is False
    assert full["error"] == "speech queue is full"


def test_mcp_surface_lists_and_accepts_the_single_speak_tool() -> None:
    from mcp import types

    service = MODULE.SpeechService(settings(default_volume=55, queue_size=2))
    server = MODULE.create_mcp_server(service)

    listed = asyncio.run(
        server.request_handlers[types.ListToolsRequest](types.ListToolsRequest())
    )
    assert [tool.name for tool in listed.root.tools] == ["tts.speak"]
    assert listed.root.tools[0].inputSchema["required"] == ["text"]

    request = types.CallToolRequest(
        params=types.CallToolRequestParams(
            name="tts.speak",
            arguments={"text": "MCP 已连接", "volume": 35},
        )
    )
    result = asyncio.run(server.request_handlers[types.CallToolRequest](request))

    assert result.root.isError is False
    assert result.root.structuredContent["accepted"] is True
    assert result.root.structuredContent["volume"] == 35
