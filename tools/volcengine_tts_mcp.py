#!/usr/bin/env python3
"""Low-latency Volcengine TTS MCP server for Ubuntu ALSA output."""

from __future__ import annotations

import argparse
from array import array
import base64
import binascii
import codecs
from dataclasses import dataclass
import http.client
import json
import logging
import os
from pathlib import Path
from queue import Empty, Full, Queue
import shutil
import ssl
import subprocess
import sys
from threading import Event, Lock, Thread
import time
from typing import Callable, Iterable, Iterator
from urllib.parse import urlparse
from uuid import uuid4


LOGGER = logging.getLogger("volcengine_tts_mcp")
DEFAULT_ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
COMPLETION_CODE = 20_000_000


class ConfigurationError(ValueError):
    """Raised when deployment configuration is invalid."""


class TtsApiError(RuntimeError):
    """Raised when Volcengine rejects or truncates a synthesis request."""


class AudioOutputError(RuntimeError):
    """Raised when ALSA playback cannot accept PCM data."""


@dataclass(frozen=True, slots=True)
class Settings:
    app_id: str
    access_token: str
    resource_id: str
    voice: str
    uid: str
    endpoint: str = DEFAULT_ENDPOINT
    audio_device: str = "default"
    default_volume: int = 80
    sample_rate: int = 24_000
    default_speech_rate: int = 0
    pitch_ratio: int = 0
    loudness_ratio: int = 0
    queue_size: int = 8
    request_timeout_s: float = 20.0
    alsa_buffer_time_us: int = 100_000
    alsa_period_time_us: int = 20_000

    @classmethod
    def from_environment(cls) -> "Settings":
        settings = cls(
            app_id=_required_environment("VOLCENGINE_APPID"),
            access_token=_required_environment("VOLCENGINE_TOKEN"),
            resource_id=os.environ.get(
                "VOLCENGINE_RESOURCE_ID", "seed-tts-2.0"
            ).strip(),
            voice=_required_environment("VOLCENGINE_TTS_VOICE"),
            uid=os.environ.get("VOLCENGINE_TTS_UID", "smart-neckband-a-process").strip(),
            endpoint=os.environ.get("VOLCENGINE_TTS_ENDPOINT", DEFAULT_ENDPOINT).strip(),
            audio_device=os.environ.get(
                "VOLCENGINE_TTS_AUDIO_DEVICE", "default"
            ).strip(),
            default_volume=_environment_int("VOLCENGINE_TTS_VOLUME", 80),
            sample_rate=_environment_int("VOLCENGINE_TTS_SAMPLE_RATE", 24_000),
            default_speech_rate=_environment_int("VOLCENGINE_TTS_SPEECH_RATE", 0),
            pitch_ratio=_environment_int("VOLCENGINE_TTS_PITCH_RATIO", 0),
            loudness_ratio=_environment_int("VOLCENGINE_TTS_LOUDNESS_RATIO", 0),
            queue_size=_environment_int("VOLCENGINE_TTS_QUEUE_SIZE", 8),
            request_timeout_s=_environment_float(
                "VOLCENGINE_TTS_REQUEST_TIMEOUT_S", 20.0
            ),
            alsa_buffer_time_us=_environment_int(
                "VOLCENGINE_TTS_ALSA_BUFFER_TIME_US", 100_000
            ),
            alsa_period_time_us=_environment_int(
                "VOLCENGINE_TTS_ALSA_PERIOD_TIME_US", 20_000
            ),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        parsed = urlparse(self.endpoint)
        if parsed.scheme != "https" or not parsed.hostname or not parsed.path:
            raise ConfigurationError("VOLCENGINE_TTS_ENDPOINT must be an HTTPS URL")
        if not self.resource_id:
            raise ConfigurationError("VOLCENGINE_RESOURCE_ID must not be empty")
        if not self.audio_device:
            raise ConfigurationError("VOLCENGINE_TTS_AUDIO_DEVICE must not be empty")
        if not 0 <= self.default_volume <= 100:
            raise ConfigurationError("VOLCENGINE_TTS_VOLUME must be in 0..100")
        if self.sample_rate not in {16_000, 24_000}:
            raise ConfigurationError(
                "VOLCENGINE_TTS_SAMPLE_RATE must be 16000 or 24000"
            )
        if not -50 <= self.default_speech_rate <= 100:
            raise ConfigurationError("VOLCENGINE_TTS_SPEECH_RATE must be in -50..100")
        if not -12 <= self.pitch_ratio <= 12:
            raise ConfigurationError("VOLCENGINE_TTS_PITCH_RATIO must be in -12..12")
        if not -6 <= self.loudness_ratio <= 6:
            raise ConfigurationError(
                "VOLCENGINE_TTS_LOUDNESS_RATIO must be in -6..6"
            )
        if not 1 <= self.queue_size <= 100:
            raise ConfigurationError("VOLCENGINE_TTS_QUEUE_SIZE must be in 1..100")
        if not 1.0 <= self.request_timeout_s <= 120.0:
            raise ConfigurationError(
                "VOLCENGINE_TTS_REQUEST_TIMEOUT_S must be in 1..120"
            )
        if self.alsa_period_time_us <= 0 or self.alsa_buffer_time_us <= 0:
            raise ConfigurationError("ALSA buffer and period times must be positive")
        if self.alsa_period_time_us > self.alsa_buffer_time_us:
            raise ConfigurationError("ALSA period time must not exceed buffer time")


@dataclass(frozen=True, slots=True)
class SpeechJob:
    request_id: str
    text: str
    volume: int
    speech_rate: int


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigurationError(f"{name} is required")
    return value


def _environment_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc


def _environment_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc


def iter_concatenated_json(chunks: Iterable[bytes]) -> Iterator[dict[str, object]]:
    """Decode the concatenated JSON objects returned by the V3 HTTP stream."""

    utf8 = codecs.getincrementaldecoder("utf-8")()
    decoder = json.JSONDecoder()
    buffer = ""

    def drain(final: bool) -> Iterator[dict[str, object]]:
        nonlocal buffer
        while True:
            buffer = buffer.lstrip()
            if not buffer:
                return
            try:
                value, end = decoder.raw_decode(buffer)
            except json.JSONDecodeError:
                if final:
                    raise TtsApiError("Volcengine returned incomplete JSON")
                return
            if not isinstance(value, dict):
                raise TtsApiError("Volcengine returned a non-object JSON frame")
            buffer = buffer[end:]
            yield value

    for chunk in chunks:
        if not chunk:
            continue
        buffer += utf8.decode(chunk)
        yield from drain(False)
    buffer += utf8.decode(b"", final=True)
    yield from drain(True)


def scale_pcm_s16le(pcm: bytes, volume: int) -> bytes:
    """Apply a 0..100 software gain to signed 16-bit little-endian PCM."""

    if not 0 <= volume <= 100:
        raise ValueError("volume must be in 0..100")
    if len(pcm) % 2:
        raise ValueError("S16_LE PCM must contain an even number of bytes")
    if volume == 100 or not pcm:
        return pcm
    if volume == 0:
        return bytes(len(pcm))

    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    gain = volume / 100.0
    for index, sample in enumerate(samples):
        samples[index] = max(-32_768, min(32_767, round(sample * gain)))
    if sys.byteorder != "little":
        samples.byteswap()
    return samples.tobytes()


def scan_alsa_devices(aplay_path: str, timeout_s: float = 3.0) -> tuple[str, str]:
    """Return ALSA PCM names and physical-card output for startup diagnostics."""

    outputs: list[str] = []
    for arguments in (("-L",), ("-l",)):
        completed = subprocess.run(
            [aplay_path, *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
        )
        outputs.append(completed.stdout.strip())
    return outputs[0], outputs[1]


class AplaySink:
    """Persistent ALSA raw-PCM sink used to avoid per-utterance device startup."""

    def __init__(
        self,
        *,
        aplay_path: str,
        device: str,
        sample_rate: int,
        buffer_time_us: int,
        period_time_us: int,
    ) -> None:
        self.aplay_path = aplay_path
        self.device = device
        self.sample_rate = sample_rate
        self.buffer_time_us = buffer_time_us
        self.period_time_us = period_time_us
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = Lock()

    @property
    def command(self) -> list[str]:
        return [
            self.aplay_path,
            "-q",
            "-D",
            self.device,
            "-t",
            "raw",
            "-f",
            "S16_LE",
            "-r",
            str(self.sample_rate),
            "-c",
            "1",
            f"--buffer-time={self.buffer_time_us}",
            f"--period-time={self.period_time_us}",
        ]

    def start(self) -> None:
        with self._lock:
            self._start_unlocked()

    def _start_unlocked(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        self._process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=sys.stderr,
            bufsize=0,
        )

    def write(self, pcm: bytes) -> None:
        if not pcm:
            return
        with self._lock:
            self._start_unlocked()
            assert self._process is not None
            if self._process.stdin is None:
                raise AudioOutputError("aplay stdin is unavailable")
            try:
                self._process.stdin.write(pcm)
            except (BrokenPipeError, OSError) as exc:
                exit_code = self._process.poll()
                raise AudioOutputError(
                    f"aplay stopped while writing PCM (exit={exit_code})"
                ) from exc

    def close(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
            if process is None:
                return
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1.0)


class VolcengineTtsClient:
    """Persistent HTTPS client for V3 unidirectional streaming TTS."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._parsed_endpoint = urlparse(settings.endpoint)
        self._connection: http.client.HTTPSConnection | None = None
        self._ssl_context = ssl.create_default_context()

    def request_payload(self, text: str, speech_rate: int) -> dict[str, object]:
        return {
            "user": {"uid": self.settings.uid},
            "req_params": {
                "text": text,
                "speaker": self.settings.voice,
                "audio_params": {
                    "format": "pcm",
                    "sample_rate": self.settings.sample_rate,
                    "speech_rate": speech_rate,
                    "pitch_ratio": self.settings.pitch_ratio,
                    "loudness_ratio": self.settings.loudness_ratio,
                },
            },
        }

    def stream_pcm(
        self,
        text: str,
        speech_rate: int,
        write_pcm: Callable[[bytes], None],
    ) -> None:
        retry_delays = (0.0, 0.2, 0.8)
        last_error: Exception | None = None
        for attempt, delay_s in enumerate(retry_delays, start=1):
            if delay_s:
                time.sleep(delay_s)
            audio_started = False

            def tracked_write(pcm: bytes) -> None:
                nonlocal audio_started
                audio_started = True
                write_pcm(pcm)

            try:
                self._stream_once(text, speech_rate, tracked_write)
                return
            except TtsApiError:
                self.close()
                raise
            except (OSError, TimeoutError, http.client.HTTPException) as exc:
                last_error = exc
                self.close()
                if audio_started:
                    raise TtsApiError(
                        "Volcengine transport stopped after playback began; "
                        "the request was not replayed to avoid duplicate speech"
                    ) from exc
                LOGGER.warning(
                    "Volcengine transport attempt %d/%d failed: %s",
                    attempt,
                    len(retry_delays),
                    exc,
                )
            except Exception:
                self.close()
                raise
        assert last_error is not None
        raise TtsApiError("Volcengine transport failed after retries") from last_error

    def _stream_once(
        self,
        text: str,
        speech_rate: int,
        write_pcm: Callable[[bytes], None],
    ) -> None:
        connection = self._get_connection()
        body = json.dumps(
            self.request_payload(text, speech_rate),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "Connection": "keep-alive",
            "X-Api-App-Key": self.settings.app_id,
            "X-Api-Access-Key": self.settings.access_token,
            "X-Api-Resource-Id": self.settings.resource_id,
            "X-Api-Connect-Id": str(uuid4()),
        }
        path = self._parsed_endpoint.path
        if self._parsed_endpoint.query:
            path += "?" + self._parsed_endpoint.query
        connection.request("POST", path, body=body, headers=headers)
        response = connection.getresponse()
        if response.status != 200:
            error_body = response.read(4096).decode("utf-8", errors="replace")
            if response.will_close:
                self.close()
            raise TtsApiError(
                f"Volcengine HTTP {response.status}: {error_body[:512]}"
            )

        completion_seen = False
        audio_frames = 0
        pending_pcm = b""

        def response_chunks() -> Iterator[bytes]:
            while True:
                chunk = response.read1(64 * 1024)
                if not chunk:
                    return
                yield chunk

        for frame in iter_concatenated_json(response_chunks()):
            try:
                code = int(frame.get("code", -1))
            except (TypeError, ValueError) as exc:
                raise TtsApiError("Volcengine frame code is invalid") from exc
            if code == 0:
                encoded_audio = frame.get("data")
                if encoded_audio is None or encoded_audio == "":
                    continue
                if not isinstance(encoded_audio, str):
                    raise TtsApiError("Volcengine audio frame data is not base64 text")
                try:
                    pcm = base64.b64decode(encoded_audio, validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise TtsApiError("Volcengine audio frame has invalid base64") from exc
                pcm = pending_pcm + pcm
                pending_pcm = pcm[-1:] if len(pcm) % 2 else b""
                pcm = pcm[:-1] if pending_pcm else pcm
                if pcm:
                    write_pcm(pcm)
                    audio_frames += 1
            elif code == COMPLETION_CODE:
                completion_seen = True
            else:
                message = frame.get("message") or frame.get("msg") or "unknown error"
                raise TtsApiError(f"Volcengine TTS error {code}: {message}")

        if pending_pcm:
            raise TtsApiError("Volcengine returned an incomplete S16_LE sample")
        if not completion_seen:
            raise TtsApiError("Volcengine stream ended without completion frame")
        if audio_frames == 0:
            raise TtsApiError(
                "Volcengine returned no audio; check text, voice permission, and resource ID"
            )
        if response.will_close:
            self.close()

    def _get_connection(self) -> http.client.HTTPSConnection:
        if self._connection is None:
            self._connection = http.client.HTTPSConnection(
                self._parsed_endpoint.hostname,
                self._parsed_endpoint.port or 443,
                timeout=self.settings.request_timeout_s,
                context=self._ssl_context,
            )
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None


class SpeechService:
    """Own the bounded request queue, streaming client, and ALSA worker."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: VolcengineTtsClient | None = None,
        sink: AplaySink | None = None,
        aplay_path: str | None = None,
    ) -> None:
        self.settings = settings
        self.aplay_path = aplay_path or shutil.which("aplay") or ""
        self.client = client or VolcengineTtsClient(settings)
        self.sink = sink
        self.queue: Queue[SpeechJob | None] = Queue(maxsize=settings.queue_size)
        self._stop = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        if self.sink is None:
            if not self.aplay_path:
                raise ConfigurationError(
                    "aplay was not found; install Ubuntu package alsa-utils"
                )
            pcm_names, physical_cards = scan_alsa_devices(self.aplay_path)
            LOGGER.info("ALSA PCM scan (-L):\n%s", pcm_names or "<no entries>")
            LOGGER.info("ALSA card scan (-l):\n%s", physical_cards or "<no entries>")
            self.sink = AplaySink(
                aplay_path=self.aplay_path,
                device=self.settings.audio_device,
                sample_rate=self.settings.sample_rate,
                buffer_time_us=self.settings.alsa_buffer_time_us,
                period_time_us=self.settings.alsa_period_time_us,
            )
        self.sink.start()
        self._thread = Thread(
            target=self._worker,
            name="volcengine-tts-playback",
            daemon=True,
        )
        self._thread.start()
        LOGGER.info(
            "TTS MCP ready: device=%s sample_rate=%d volume=%d queue=%d",
            self.settings.audio_device,
            self.settings.sample_rate,
            self.settings.default_volume,
            self.settings.queue_size,
        )

    def enqueue(self, arguments: dict[str, object]) -> dict[str, object]:
        text = arguments.get("text")
        if not isinstance(text, str) or not text.strip():
            return self._error_result("text must be a non-empty string")
        if len(text) > 2_000:
            return self._error_result("text must contain at most 2000 characters")
        volume = arguments.get("volume", self.settings.default_volume)
        speech_rate = arguments.get(
            "speech_rate", self.settings.default_speech_rate
        )
        if isinstance(volume, bool) or not isinstance(volume, int) or not 0 <= volume <= 100:
            return self._error_result("volume must be an integer in 0..100")
        if (
            isinstance(speech_rate, bool)
            or not isinstance(speech_rate, int)
            or not -50 <= speech_rate <= 100
        ):
            return self._error_result("speech_rate must be an integer in -50..100")
        unexpected = set(arguments) - {"text", "volume", "speech_rate"}
        if unexpected:
            return self._error_result(
                "unsupported arguments: " + ", ".join(sorted(unexpected))
            )

        job = SpeechJob(
            request_id=str(uuid4()),
            text=text.strip(),
            volume=volume,
            speech_rate=speech_rate,
        )
        try:
            self.queue.put_nowait(job)
        except Full:
            return self._error_result("speech queue is full")
        return {
            "accepted": True,
            "request_id": job.request_id,
            "queue_depth": self.queue.qsize(),
            "volume": job.volume,
            "speech_rate": job.speech_rate,
            "error": None,
        }

    def _error_result(self, message: str) -> dict[str, object]:
        return {
            "accepted": False,
            "request_id": None,
            "queue_depth": self.queue.qsize(),
            "volume": self.settings.default_volume,
            "speech_rate": self.settings.default_speech_rate,
            "error": message,
        }

    def _worker(self) -> None:
        assert self.sink is not None
        while not self._stop.is_set():
            try:
                job = self.queue.get(timeout=0.2)
            except Empty:
                continue
            try:
                if job is None:
                    return
                started = time.monotonic()
                self.client.stream_pcm(
                    job.text,
                    job.speech_rate,
                    lambda pcm: self.sink.write(
                        scale_pcm_s16le(pcm, job.volume)
                    ),
                )
                LOGGER.info(
                    "request_id=%s playback stream completed in %.3fs",
                    job.request_id,
                    time.monotonic() - started,
                )
            except Exception:
                request_id = job.request_id if isinstance(job, SpeechJob) else "shutdown"
                LOGGER.exception("request_id=%s synthesis/playback failed", request_id)
            finally:
                self.queue.task_done()

    def close(self) -> None:
        self._stop.set()
        try:
            self.queue.put_nowait(None)
        except Full:
            pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self.client.close()
        if self.sink is not None:
            self.sink.close()


def create_mcp_server(service: SpeechService):
    from mcp import types
    from mcp.server.lowlevel import Server
    from mcp.shared.exceptions import McpError

    server = Server(
        "volcengine-tts-audio",
        version="1.0.0",
        instructions=(
            "Queue text for low-latency speech on the Ubuntu default ALSA output. "
            "A successful call means accepted for asynchronous playback."
        ),
    )

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="tts.speak",
                title="Speak text through the local audio output",
                description=(
                    "Queue complete text for Volcengine synthesis and local ALSA "
                    "playback. Returns immediately after queue admission."
                ),
                inputSchema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["text"],
                    "properties": {
                        "text": {"type": "string", "minLength": 1, "maxLength": 2000},
                        "volume": {"type": "integer", "minimum": 0, "maximum": 100},
                        "speech_rate": {
                            "type": "integer",
                            "minimum": -50,
                            "maximum": 100,
                        },
                    },
                },
                outputSchema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "accepted",
                        "request_id",
                        "queue_depth",
                        "volume",
                        "speech_rate",
                        "error",
                    ],
                    "properties": {
                        "accepted": {"type": "boolean"},
                        "request_id": {"type": ["string", "null"]},
                        "queue_depth": {"type": "integer", "minimum": 0},
                        "volume": {"type": "integer", "minimum": 0, "maximum": 100},
                        "speech_rate": {
                            "type": "integer",
                            "minimum": -50,
                            "maximum": 100,
                        },
                        "error": {"type": ["string", "null"]},
                    },
                },
                annotations=types.ToolAnnotations(
                    title="Speak text",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=True,
                ),
            )
        ]

    async def call_tool_handler(req: types.CallToolRequest):
        if req.params.name != "tts.speak":
            raise McpError(
                types.ErrorData(
                    code=types.INVALID_PARAMS,
                    message=f"Unknown tool: {req.params.name}",
                )
            )
        result = service.enqueue(req.params.arguments or {})
        serialized = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        return types.ServerResult(
            types.CallToolResult(
                content=[types.TextContent(type="text", text=serialized)],
                structuredContent=result,
                isError=not bool(result["accepted"]),
            )
        )

    server.request_handlers[types.CallToolRequest] = call_tool_handler
    return server


async def run_stdio(service: SpeechService) -> None:
    from mcp.server.stdio import stdio_server

    server = create_mcp_server(service)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def _load_dotenv(env_file: Path | None) -> None:
    try:
        from dotenv import load_dotenv
    except ImportError as exc:
        raise ConfigurationError(
            "python-dotenv is required; install tools/volcengine_tts_mcp.requirements.txt"
        ) from exc
    if env_file is not None:
        if not env_file.is_file():
            raise ConfigurationError(f"environment file does not exist: {env_file}")
        load_dotenv(env_file, override=False)
    else:
        load_dotenv(override=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Volcengine TTS MCP server with direct Ubuntu ALSA playback"
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Optional .env file; defaults to python-dotenv discovery",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        _load_dotenv(args.env_file)
        settings = Settings.from_environment()
        service = SpeechService(settings)
        service.start()
    except (ConfigurationError, OSError, subprocess.SubprocessError) as exc:
        parser.error(str(exc))

    import asyncio

    try:
        asyncio.run(run_stdio(service))
    finally:
        service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
