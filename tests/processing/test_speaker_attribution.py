from stt_vault.core.models.records import SpeakerSegment, TranscriptSegment
from stt_vault.processing.speaker_attribution import attribute_speakers


def test_text_only_chinese_is_allocated_without_losing_characters() -> None:
    segments = attribute_speakers(
        [TranscriptSegment(0, 8, "UNKNOWN", "你好世界。再见！", chunk_start=0, chunk_end=8)],
        [SpeakerSegment(0, 4, "A"), SpeakerSegment(4, 8, "B")],
    )

    assert [(s.speaker, s.text, s.start, s.end) for s in segments] == [
        ("A", "你好世界", 0, 4),
        ("B", "。再见！", 4, 8),
    ]

    assert [(s.chunk_start, s.chunk_end) for s in segments] == [(0, 4), (4, 8)]
