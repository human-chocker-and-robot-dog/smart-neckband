from smart_neckband.protocol import (
    VOICE_TEXT_FLAG_FINAL,
    VoiceTextChunkPayload,
)
from smart_neckband.voice import VoiceTextAssembler


def _chunk(
    utterance_id: int,
    index: int,
    count: int,
    data: bytes,
    *,
    final: bool = True,
) -> VoiceTextChunkPayload:
    return VoiceTextChunkPayload(
        utterance_id=utterance_id,
        chunk_index=index,
        chunk_count=count,
        text_bytes=data,
        flags=VOICE_TEXT_FLAG_FINAL if final else 0,
    )


def test_reassembles_chinese_split_inside_utf8_codepoint() -> None:
    raw = "主人主人，请向前走两米。".encode("utf-8")
    assembler = VoiceTextAssembler()

    assert assembler.add_chunk(_chunk(0x12, 1, 2, raw[17:])) is None
    transcript = assembler.add_chunk(_chunk(0x12, 0, 2, raw[:17]))

    assert transcript is not None
    assert transcript.text == "主人主人，请向前走两米。"
    assert transcript.instruction_id == "voice-0000000000000012"


def test_duplicate_same_chunks_are_ackable_but_conflict_is_not() -> None:
    raw = "向前走".encode("utf-8")
    assembler = VoiceTextAssembler()

    original = assembler.add_chunk(_chunk(7, 0, 1, raw))
    duplicate = assembler.add_chunk(_chunk(7, 0, 1, raw))
    conflict = assembler.add_chunk(_chunk(7, 0, 1, "停止".encode()))

    assert original is not None and not original.duplicate
    assert duplicate is not None and duplicate.duplicate
    assert conflict is None
    assert assembler.stats.duplicates == 1
    assert assembler.stats.conflicts == 1


def test_multichunk_duplicate_is_ackable_only_after_full_cycle() -> None:
    assembler = VoiceTextAssembler()
    assert assembler.add_chunk(_chunk(10, 0, 2, b"first")) is None
    assert assembler.add_chunk(_chunk(10, 1, 2, b"second")) is not None

    assert assembler.add_chunk(_chunk(10, 1, 2, b"second")) is None
    duplicate = assembler.add_chunk(_chunk(10, 0, 2, b"first"))

    assert duplicate is not None and duplicate.duplicate


def test_rejects_invalid_utf8_and_never_completes_missing_chunk() -> None:
    assembler = VoiceTextAssembler(expiry_s=0.0)

    assert assembler.add_chunk(_chunk(8, 0, 1, b"\xff")) is None
    assert assembler.stats.invalid_utf8 == 1

    assembler = VoiceTextAssembler()
    assert assembler.add_chunk(_chunk(9, 0, 2, b"partial")) is None
    assert assembler.pending_count == 1
