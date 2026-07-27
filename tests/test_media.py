import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from stt_vault.core.process_diagnostics import (
    MAX_SUBPROCESS_DIAGNOSTIC_BYTES,
    format_process_diagnostics,
)
from stt_vault.processing.media import (
    ffprobe_audio_streams,
    ffprobe_duration,
    move_upload,
    store_upload,
)


@pytest.mark.parametrize("operation", [store_upload, move_upload])
def test_upload_storage_uses_a_single_destination_and_metadata_setup(
    operation, tmp_path: Path
) -> None:
    source = tmp_path / "source.wav"
    source.write_bytes(b"audio")

    asset_id, stored_path, media_type = operation(tmp_path / "media", "clip.wav", source)

    assert stored_path == tmp_path / "media" / asset_id / "clip.wav"
    assert stored_path.read_bytes() == b"audio"
    assert media_type == "audio"


def test_ffprobe_duration_uses_injected_command_runner() -> None:
    commands: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(stdout=json.dumps({"format": {"duration": "12.5"}}))

    assert ffprobe_duration(Path("recording.wav"), runner=run) == 12.5
    assert commands == [
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            "recording.wav",
        ]
    ]


@pytest.mark.parametrize(
    ("stdout", "message"),
    [
        ("[]", "JSON object"),
        ("{invalid", "invalid JSON"),
        (json.dumps({"format": {}}), "numeric duration"),
    ],
)
def test_ffprobe_duration_rejects_malformed_response(stdout: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        ffprobe_duration(
            Path("recording.wav"), runner=lambda *_args, **_kwargs: SimpleNamespace(stdout=stdout)
        )


def test_ffprobe_audio_streams_rejects_malformed_stream_shape() -> None:
    result = SimpleNamespace(stdout=json.dumps({"streams": {"index": 0}}))

    with pytest.raises(ValueError, match="streams collection"):
        ffprobe_audio_streams(Path("recording.wav"), runner=lambda *_args, **_kwargs: result)


def test_process_diagnostic_formatter_redacts_and_bounds_credentials() -> None:
    diagnostic = format_process_diagnostics(
        b"authorization=Bearer secret-value\n" + b"x" * (MAX_SUBPROCESS_DIAGNOSTIC_BYTES + 1)
    )

    assert "secret-value" not in diagnostic
    assert "[redacted]" in diagnostic
    assert diagnostic.endswith("[truncated]")
