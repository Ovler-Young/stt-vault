from stt_vault.processing.speaker_attribution import attribute_speakers


def test_text_only_attribution_preserves_chinese_and_playback_bounds() -> None:
    segments = attribute_speakers(
        [{"start": 0, "end": 8, "speaker": "UNKNOWN", "text": "你好世界。再见！"}],
        [{"start": 0, "end": 4, "speaker": "A"}, {"start": 4, "end": 8, "speaker": "B"}],
    )
    assert [(s["speaker"], s["text"], s["chunk_start"], s["chunk_end"]) for s in segments] == [
        ("A", "你好世界", 0, 4),
        ("B", "。再见！", 4, 8),
    ]
