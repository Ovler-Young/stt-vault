from stt_vault.exports import to_ai_text
from stt_vault.transcription import (
    build_chunks,
    build_transcription_plan,
    transcript_chunks_match_plan,
)


def test_build_chunks_preserves_exact_60_second_request_windows() -> None:
    chunks = build_chunks(
        [{"start": 10.0, "end": 145.0, "speaker": "SPEAKER_01"}],
        max_seconds=60,
    )

    assert chunks == [
        {
            "chunk_index": 0,
            "start": 10.0,
            "end": 70.0,
            "speaker": "SPEAKER_01",
        },
        {
            "chunk_index": 1,
            "start": 70.0,
            "end": 130.0,
            "speaker": "SPEAKER_01",
        },
        {
            "chunk_index": 2,
            "start": 130.0,
            "end": 145.0,
            "speaker": "SPEAKER_01",
        },
    ]


def test_transcription_plan_uses_raw_speaker_turns() -> None:
    chunks = build_transcription_plan(
        {
            "raw_segments": [
                {"start": 0.0, "end": 20.0, "speaker": "SPEAKER_01"},
                {"start": 20.0, "end": 30.0, "speaker": "SPEAKER_02"},
            ],
            "merged_segments": [{"start": 0.0, "end": 30.0, "speaker": "SPEAKER_01"}],
        },
        max_seconds=60,
    )

    assert [(chunk["start"], chunk["end"], chunk["speaker"]) for chunk in chunks] == [
        (0.0, 20.0, "SPEAKER_01"),
        (20.0, 30.0, "SPEAKER_02"),
    ]


def test_transcript_chunks_require_the_current_request_plan() -> None:
    chunks = build_chunks(
        [{"start": 0.0, "end": 75.0, "speaker": "SPEAKER_01"}],
        max_seconds=60,
    )

    assert transcript_chunks_match_plan(
        [
            {
                "chunk_index": 0,
                "start": 0.0,
                "end": 60.0,
                "chunk_start": 0.0,
                "chunk_end": 60.0,
                "speaker": "SPEAKER_01",
            }
        ],
        chunks,
    )
    assert not transcript_chunks_match_plan(
        [
            {
                "chunk_index": 0,
                "start": 0.0,
                "end": 75.0,
                "chunk_start": 0.0,
                "chunk_end": 60.0,
                "speaker": "SPEAKER_01",
            }
        ],
        chunks,
    )


def test_ai_text_keeps_each_transcription_request_separate() -> None:
    text = to_ai_text(
        [
            {"start": 0.0, "end": 10.0, "speaker": "SPEAKER_01", "text": "First turn."},
            {"start": 10.0, "end": 20.0, "speaker": "SPEAKER_01", "text": "Second turn."},
        ]
    )

    assert text == (
        "[00:00:00.000] SPEAKER_01:\nFirst turn.\n\n"
        "[00:00:10.000] SPEAKER_01:\nSecond turn.\n"
    )
