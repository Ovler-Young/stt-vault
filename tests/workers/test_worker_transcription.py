import pytest

from stt_vault.workers.worker_transcription import TranscriberConfig, create_transcriber


def test_create_transcriber_maps_every_config_field(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeTranscriber:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("stt_vault.workers.worker_transcription.Transcriber", FakeTranscriber)

    def on_chunk_done(_index, _result):
        return None

    def on_chunk_retry(_index, _attempt, _error, _retry_at):
        return None

    config = TranscriberConfig(
        provider="mod-whisper-cpu",
        api_key="api-key",
        base_url="https://example.test/v1",
        model="transcription-model",
        prompt="Use speaker labels",
        concurrency=3,
        retry_seconds=17,
        max_retries=4,
        retry_backoff_seconds=[2, 5, 11],
        on_chunk_done=on_chunk_done,
        on_chunk_retry=on_chunk_retry,
    )

    create_transcriber(config)

    assert captured == {
        "provider": "mod-whisper-cpu",
        "api_key": "api-key",
        "base_url": "https://example.test/v1",
        "model": "transcription-model",
        "prompt": "Use speaker labels",
        "concurrency": 3,
        "retry_seconds": 17,
        "max_retries": 4,
        "retry_backoff_seconds": [2, 5, 11],
        "on_chunk_done": on_chunk_done,
        "on_chunk_retry": on_chunk_retry,
    }
