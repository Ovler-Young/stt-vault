import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from stt_vault.core.diagnostics.process import (
    MAX_SUBPROCESS_DIAGNOSTIC_BYTES,
    format_process_diagnostics,
)
from stt_vault.processing.media_probe import (
    ffprobe_audio_streams,
    ffprobe_duration,
    ffprobe_media_type,
)


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


def test_ffprobe_audio_streams_rejects_invalid_typed_stream_field() -> None:
    result = SimpleNamespace(stdout=json.dumps({"streams": [{"channels": "two"}]}))

    with pytest.raises(ValueError, match="stream channels"):
        ffprobe_audio_streams(Path("recording.wav"), runner=lambda *_args, **_kwargs: result)


@pytest.mark.parametrize(
    ("streams", "expected"),
    [
        ([{"codec_type": "audio", "disposition": {"attached_pic": 0}}], "audio"),
        (
            [
                {"codec_type": "audio", "disposition": {"attached_pic": 0}},
                {"codec_type": "video", "disposition": {"attached_pic": 1}},
            ],
            "audio",
        ),
        (
            [
                {"codec_type": "audio", "disposition": {"attached_pic": 0}},
                {"codec_type": "video", "disposition": {"attached_pic": 0}},
            ],
            "video",
        ),
    ],
)
def test_ffprobe_media_type_classifies_stream_content(
    streams: list[dict[str, str]], expected: str
) -> None:
    result = SimpleNamespace(stdout=json.dumps({"streams": streams}))

    assert (
        ffprobe_media_type(Path("misleading.extension"), runner=lambda *_args, **_kwargs: result)
        == expected
    )


def test_ffprobe_media_type_rejects_cover_art_without_audio() -> None:
    result = SimpleNamespace(
        stdout=json.dumps(
            {"streams": [{"codec_type": "video", "disposition": {"attached_pic": 1}}]}
        )
    )

    with pytest.raises(ValueError, match="no audio or video streams"):
        ffprobe_media_type(Path("cover-only.m4a"), runner=lambda *_args, **_kwargs: result)


def test_ffprobe_media_type_rejects_malformed_attached_picture_disposition() -> None:
    result = SimpleNamespace(
        stdout=json.dumps(
            {"streams": [{"codec_type": "audio", "disposition": {"attached_pic": "1"}}]}
        )
    )

    with pytest.raises(ValueError, match="attached_pic"):
        ffprobe_media_type(Path("recording.m4a"), runner=lambda *_args, **_kwargs: result)


def test_ffprobe_media_type_rejects_files_without_playable_streams() -> None:
    result = SimpleNamespace(
        stdout=json.dumps(
            {"streams": [{"codec_type": "subtitle", "disposition": {"attached_pic": 0}}]}
        )
    )

    with pytest.raises(ValueError, match="no audio or video streams"):
        ffprobe_media_type(Path("input.bin"), runner=lambda *_args, **_kwargs: result)


def test_process_diagnostic_formatter_redacts_and_bounds_credentials() -> None:
    diagnostic = format_process_diagnostics(
        b"authorization=Bearer secret-value\n" + b"x" * (MAX_SUBPROCESS_DIAGNOSTIC_BYTES + 1)
    )

    assert "secret-value" not in diagnostic
    assert "[redacted]" in diagnostic
    assert diagnostic.endswith("[truncated]")
