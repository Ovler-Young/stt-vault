import pytest


@pytest.fixture(autouse=True)
def select_default_test_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STT_TRANSCRIPTION_PROVIDER", "openai")
    monkeypatch.setenv("STT_DIARIZATION_PROVIDER", "senko")
