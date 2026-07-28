from stt_vault.processing.exports import to_ai_text
from stt_vault.processing.transcription import (
    Transcriber,
    build_chunks,
    build_transcription_plan,
    transcript_chunks_match_plan,
)


def test_transcriber_uses_injected_client_and_chunk_extractor(tmp_path) -> None:
    extracted_chunks: list[tuple[float, float]] = []

    def extract_chunk(_media_path, output_path, start, end):
        extracted_chunks.append((start, end))
        output_path.write_bytes(b"audio")
        return output_path

    class FakeTranscriptions:
        def create(self, **_kwargs):
            return {"text": "spoken words"}

    class FakeClient:
        audio = type("Audio", (), {"transcriptions": FakeTranscriptions()})()

    transcriber = Transcriber(
        api_key="unused",
        base_url="unused",
        model="unused",
        prompt="",
        concurrency=1,
        retry_seconds=1,
        max_retries=1,
        client=FakeClient(),
        chunk_extractor=extract_chunk,
    )

    result = transcriber.transcribe_chunks(
        tmp_path / "input.wav",
        [{"start": 0.0, "end": 1.5, "speaker": "SPEAKER_01"}],
        tmp_path,
    )

    assert extracted_chunks == [(0.0, 1.5)]
    assert result[0]["text"] == "spoken words"


def test_transcriber_rejects_a_mapping_response_without_text(tmp_path) -> None:
    def extract_chunk(_media_path, output_path, _start, _end):
        output_path.write_bytes(b"audio")
        return output_path

    class FakeTranscriptions:
        def create(self, **_kwargs):
            return {"unexpected": "response"}

    class FakeClient:
        audio = type("Audio", (), {"transcriptions": FakeTranscriptions()})()

    transcriber = Transcriber(
        api_key="unused",
        base_url="unused",
        model="unused",
        prompt="",
        concurrency=1,
        retry_seconds=1,
        max_retries=1,
        client=FakeClient(),
        chunk_extractor=extract_chunk,
    )

    try:
        transcriber.transcribe_chunks(
            tmp_path / "input.wav",
            [{"start": 0.0, "end": 1.5, "speaker": "SPEAKER_01"}],
            tmp_path,
        )
    except TypeError as error:
        assert str(error) == "transcription response mapping is missing text"
    else:
        raise AssertionError("expected invalid response to fail")


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
        "[00:00:00.000] SPEAKER_01:\nFirst turn.\n\n[00:00:10.000] SPEAKER_01:\nSecond turn.\n"
    )


def test_transcriber_uses_injected_clock_and_sleeper_for_retries(tmp_path) -> None:
    sleeps: list[float] = []

    def extract_chunk(_media_path, output_path, _start, _end):
        output_path.write_bytes(b"audio")
        return output_path

    class FailingTranscriptions:
        def create(self, **_kwargs):
            raise RuntimeError("provider unavailable")

    class FakeClient:
        audio = type("Audio", (), {"transcriptions": FailingTranscriptions()})()

    transcriber = Transcriber(
        api_key="unused",
        base_url="unused",
        model="unused",
        prompt="",
        concurrency=1,
        retry_seconds=3,
        max_retries=2,
        client=FakeClient(),
        chunk_extractor=extract_chunk,
        clock=lambda: 100.0,
        sleeper=sleeps.append,
    )

    try:
        transcriber.transcribe_chunks(
            tmp_path / "input.wav",
            [{"chunk_index": 0, "start": 0.0, "end": 1.5, "speaker": "SPEAKER_01"}],
            tmp_path,
        )
    except RuntimeError as error:
        assert str(error) == "provider unavailable"
    else:
        raise AssertionError("expected retry exhaustion")
    assert sleeps == [3.0, 3.0]


def test_transcriber_retries_a_failed_attempt_then_reports_the_successful_attempt(tmp_path) -> None:
    retries: list[tuple[int, int, str, int]] = []
    extraction_count = 0

    def extract_chunk(_media_path, output_path, _start, _end):
        nonlocal extraction_count
        extraction_count += 1
        output_path.write_bytes(b"audio")
        return output_path

    class EventuallySuccessfulTranscriptions:
        def create(self, **_kwargs):
            if extraction_count == 1:
                raise RuntimeError("provider unavailable")
            return {"text": "recovered"}

    class FakeClient:
        audio = type("Audio", (), {"transcriptions": EventuallySuccessfulTranscriptions()})()

    transcriber = Transcriber(
        api_key="unused",
        base_url="unused",
        model="unused",
        prompt="",
        concurrency=1,
        retry_seconds=3,
        max_retries=2,
        client=FakeClient(),
        chunk_extractor=extract_chunk,
        clock=lambda: 100.0,
        sleeper=lambda _seconds: None,
        on_chunk_retry=lambda index, attempt, error, retry_at: retries.append(
            (index, attempt, str(error), retry_at)
        ),
    )

    result = transcriber.transcribe_chunks(
        tmp_path / "input.wav",
        [{"chunk_index": 0, "start": 0.0, "end": 1.5, "speaker": "SPEAKER_01"}],
        tmp_path,
    )

    assert extraction_count == 2
    assert retries == [(0, 1, "provider unavailable", 103)]
    assert result[0]["attempts"] == 2
    assert result[0]["text"] == "recovered"
