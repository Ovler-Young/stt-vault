import io
import subprocess
from pathlib import Path

import pytest

from stt_vault.visual import detect_slide_changes


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


def test_slide_change_failure_keeps_ffmpeg_diagnostics() -> None:
    with pytest.raises(subprocess.CalledProcessError) as error:
        detect_slide_changes(
            Path("broken.mp4"), process_factory=lambda *_args, **_kwargs: FailedFfmpeg()
        )

    assert error.value.stderr == b"invalid media"


def test_slide_change_failure_drains_noisy_stderr_before_waiting() -> None:
    process = NoisyStderrFfmpeg()

    with pytest.raises(subprocess.CalledProcessError) as error:
        detect_slide_changes(Path("broken.mp4"), process_factory=lambda _command: process)

    assert process.stderr_read is True
    assert error.value.returncode == 1
    assert len(error.value.stderr) <= 8 * 1024 + len(b" [truncated]")
