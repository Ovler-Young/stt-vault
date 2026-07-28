import json
import logging
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from stt_vault.core.diagnostics.logging import StructuredFormatter
from stt_vault.processing.diarization import DiarizerManager


def test_batched_diarization_logs_json_without_provider_console_output(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    wav_path = tmp_path / "private.wav"
    with wave.open(str(wav_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(b"\x00\x00")

    provider = SimpleNamespace(
        _validate_wav_file=lambda _wav_file, _path: None,
        _perform_vad=lambda _path: [],
    )
    manager = DiarizerManager(device="cpu", idle_timeout_seconds=1)

    with caplog.at_level(logging.INFO, logger="stt_vault.processing.diarization"):
        assert manager._diarize_batched(provider, str(wav_path)) is None

    record = caplog.records[-1]
    event = json.loads(StructuredFormatter().format(record))
    assert event["event_name"] == "diarization.started"
    assert event["media_filename"] == "private.wav"
    assert str(wav_path.parent) not in StructuredFormatter().format(record)
