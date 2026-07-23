from __future__ import annotations

from dataclasses import dataclass, field
import time

from .protocol import (
    VOICE_TEXT_CHUNK_DATA_SIZE,
    VOICE_TEXT_FLAG_FINAL,
    VOICE_TEXT_MAX_BYTES,
    VoiceTextChunkPayload,
)


@dataclass(frozen=True, slots=True)
class VoiceTranscript:
    utterance_id: int
    text: str
    duplicate: bool = False

    @property
    def instruction_id(self) -> str:
        return f"voice-{self.utterance_id:016x}"


@dataclass(slots=True)
class VoiceAssemblyStats:
    chunks_received: int = 0
    transcripts_completed: int = 0
    duplicates: int = 0
    conflicts: int = 0
    invalid_utf8: int = 0
    oversized: int = 0
    expired: int = 0


@dataclass(slots=True)
class _PendingTranscript:
    chunk_count: int
    chunks: dict[int, bytes] = field(default_factory=dict)
    final: bool = False
    updated_monotonic_s: float = field(default_factory=time.monotonic)
    conflicted: bool = False


@dataclass(frozen=True, slots=True)
class _CompletedTranscript:
    text: str
    chunks: tuple[bytes, ...]


class VoiceTextAssembler:
    """Reassemble final UTF-8 ASR text carried in fixed-size V0 chunks."""

    def __init__(self, *, expiry_s: float = 30.0) -> None:
        self.expiry_s = expiry_s
        self.stats = VoiceAssemblyStats()
        self._pending: dict[int, _PendingTranscript] = {}
        self._completed: dict[int, _CompletedTranscript] = {}
        self._duplicate_chunks: dict[int, set[int]] = {}

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def add_chunk(self, chunk: VoiceTextChunkPayload) -> VoiceTranscript | None:
        self.expire()
        self.stats.chunks_received += 1

        completed = self._completed.get(chunk.utterance_id)
        if completed is not None:
            if (
                chunk.chunk_count != len(completed.chunks)
                or completed.chunks[chunk.chunk_index] != chunk.text_bytes
            ):
                self._duplicate_chunks.pop(chunk.utterance_id, None)
                self.stats.conflicts += 1
                return None
            duplicate_chunks = self._duplicate_chunks.setdefault(
                chunk.utterance_id, set()
            )
            duplicate_chunks.add(chunk.chunk_index)
            if len(duplicate_chunks) != len(completed.chunks):
                return None
            del self._duplicate_chunks[chunk.utterance_id]
            self.stats.duplicates += 1
            return VoiceTranscript(
                utterance_id=chunk.utterance_id,
                text=completed.text,
                duplicate=True,
            )

        maximum_chunks = (
            VOICE_TEXT_MAX_BYTES + VOICE_TEXT_CHUNK_DATA_SIZE - 1
        ) // VOICE_TEXT_CHUNK_DATA_SIZE
        if chunk.chunk_count > maximum_chunks:
            self.stats.oversized += 1
            return None

        pending = self._pending.get(chunk.utterance_id)
        if pending is None:
            pending = _PendingTranscript(chunk_count=chunk.chunk_count)
            self._pending[chunk.utterance_id] = pending
        elif pending.chunk_count != chunk.chunk_count:
            pending.conflicted = True
            self.stats.conflicts += 1
            return None

        existing = pending.chunks.get(chunk.chunk_index)
        if existing is not None and existing != chunk.text_bytes:
            pending.conflicted = True
            self.stats.conflicts += 1
            return None

        pending.chunks[chunk.chunk_index] = chunk.text_bytes
        pending.final = pending.final or bool(chunk.flags & VOICE_TEXT_FLAG_FINAL)
        pending.updated_monotonic_s = time.monotonic()
        if pending.conflicted or not pending.final or len(pending.chunks) != pending.chunk_count:
            return None

        raw_text = b"".join(pending.chunks[index] for index in range(pending.chunk_count))
        if len(raw_text) > VOICE_TEXT_MAX_BYTES:
            self.stats.oversized += 1
            del self._pending[chunk.utterance_id]
            return None
        try:
            text = raw_text.decode("utf-8")
        except UnicodeDecodeError:
            self.stats.invalid_utf8 += 1
            del self._pending[chunk.utterance_id]
            return None
        if not text.strip():
            del self._pending[chunk.utterance_id]
            return None

        self._completed[chunk.utterance_id] = _CompletedTranscript(
            text=text,
            chunks=tuple(pending.chunks[index] for index in range(pending.chunk_count)),
        )
        del self._pending[chunk.utterance_id]
        self.stats.transcripts_completed += 1
        return VoiceTranscript(utterance_id=chunk.utterance_id, text=text)

    def expire(self, *, now_monotonic_s: float | None = None) -> None:
        now = time.monotonic() if now_monotonic_s is None else now_monotonic_s
        expired_ids = [
            utterance_id
            for utterance_id, pending in self._pending.items()
            if now - pending.updated_monotonic_s >= self.expiry_s
        ]
        for utterance_id in expired_ids:
            del self._pending[utterance_id]
            self.stats.expired += 1
