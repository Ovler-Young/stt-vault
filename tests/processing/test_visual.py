import io
from pathlib import Path

import pytest

from stt_vault.core.models.records import VisualEvent
from stt_vault.processing.visual import (
    VisualProcessingError,
    detect_slide_changes,
    write_visual_event_thumbnails,
)


class FailedFfmpeg:
    stdout = None
    stderr = None

    def __init__(self) -> None:
        self.stdout = type(
            "Output", (), {"read": lambda _self, _size: b"", "close": lambda _self: None}
        )()
        self.stderr = io.BytesIO(b"invalid media")

    def wait(self) -> int:
        return 1


class NoisyStderrFfmpeg:
    def __init__(self) -> None:
        self.stderr_read = False
        self._stderr = b"ffmpeg diagnostic\n" * 10_000
        self.stdout = type(
            "Output", (), {"read": lambda _self, _size: b"", "close": lambda _self: None}
        )()
        self.stderr = type(
            "Errors",
            (),
            {
                "read": lambda instance, _size=-1: self._read_stderr(instance),
                "close": lambda _self: None,
            },
        )()

    def _read_stderr(self, _instance) -> bytes:
        if self.stderr_read:
            return b""
        self.stderr_read = True
        return self._stderr

    def wait(self) -> int:
        if not self.stderr_read:
            raise RuntimeError("stderr was not drained before waiting")
        return 1


class ReadFailureFfmpeg:
    def __init__(self) -> None:
        self.waited = False
        self.stdout_closed = False
        self.stderr_closed = False
        self.stdout = type(
            "Output",
            (),
            {
                "read": lambda _self, _size: (_ for _ in ()).throw(OSError("frame read failed")),
                "close": lambda _self: setattr(self, "stdout_closed", True),
            },
        )()
        self.stderr = type(
            "Errors",
            (),
            {
                "read": lambda _self, _size=-1: b"",
                "close": lambda _self: setattr(self, "stderr_closed", True),
            },
        )()

    def wait(self) -> int:
        self.waited = True
        return 0


def test_slide_change_failure_does_not_expose_ffmpeg_diagnostics(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "Bearer diagnostic-secret /srv/private/broken.mp4"
    process = FailedFfmpeg()
    process.stderr = io.BytesIO(secret.encode())

    with pytest.raises(VisualProcessingError) as error:
        detect_slide_changes(Path("broken.mp4"), process_factory=lambda *_args, **_kwargs: process)

    assert secret not in str(error.value)
    assert "diagnostic-secret" not in caplog.text
    assert "/srv/private/broken.mp4" not in caplog.text


def test_slide_change_failure_drains_noisy_stderr_before_waiting() -> None:
    process = NoisyStderrFfmpeg()

    with pytest.raises(VisualProcessingError) as error:
        detect_slide_changes(Path("broken.mp4"), process_factory=lambda _command: process)

    assert process.stderr_read is True
    assert error.value.return_code == 1


def test_slide_change_read_failure_waits_joins_and_closes_pipes() -> None:
    process = ReadFailureFfmpeg()

    with pytest.raises(OSError, match="frame read failed"):
        detect_slide_changes(Path("broken.mp4"), process_factory=lambda _command: process)

    assert process.waited is True
    assert process.stdout_closed is True
    assert process.stderr_closed is True


def test_write_visual_event_thumbnails_uses_injected_extractor(tmp_path: Path) -> None:
    calls: list[tuple[Path, Path, float]] = []

    write_visual_event_thumbnails(
        tmp_path / "clip.mp4",
        tmp_path / "exports",
        "asset-1",
        [VisualEvent(2.5, 20.0, "slide_change")],
        extractor=lambda media_path, output_path, timestamp, _runner: (
            calls.append((media_path, output_path, timestamp)),
            output_path,
        )[1],
    )

    assert calls == [
        (
            tmp_path / "clip.mp4",
            tmp_path / "exports" / "asset-1" / "visual-thumbnails" / "event-0000.jpg",
            2.5,
        )
    ]
