import io
import logging

from stt_vault.media_streaming import stream_process_stdout
from stt_vault.process_diagnostics import MAX_SUBPROCESS_DIAGNOSTIC_BYTES


class CountingBytesIO(io.BytesIO):
    def __init__(self, value: bytes) -> None:
        super().__init__(value)
        self.read_bytes = 0

    def read(self, size: int = -1) -> bytes:
        value = super().read(size)
        self.read_bytes += len(value)
        return value


class FakeProcess:
    def __init__(self, stdout: bytes, stderr: bytes = b"", return_code: int = 0) -> None:
        self.stdout = io.BytesIO(stdout)
        self.stderr = CountingBytesIO(stderr)
        self.return_code = return_code
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return 0 if self.terminated else None

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: int | None = None) -> int:
        return self.return_code


def test_stream_process_stdout_yields_process_output_from_injected_factory() -> None:
    process = FakeProcess(b"streamed media")
    commands: list[list[str]] = []

    assert list(
        stream_process_stdout(
            ["ffmpeg", "-version"], lambda command: (commands.append(command), process)[1]
        )
    ) == [b"streamed media"]
    assert commands == [["ffmpeg", "-version"]]


def test_stream_process_stdout_logs_bounded_failure_diagnostics(caplog) -> None:
    process = FakeProcess(b"", b"token=secret\n" + b"x" * (MAX_SUBPROCESS_DIAGNOSTIC_BYTES + 1), 1)

    with caplog.at_level(logging.ERROR):
        assert (
            list(stream_process_stdout(["ffmpeg", "/private/video.mp4"], lambda _command: process))
            == []
        )

    record = next(
        record for record in caplog.records if record.message == "media streaming process failed"
    )
    assert record.return_code == 1
    assert record.command == "ffmpeg"
    assert "secret" not in record.stderr
    assert "[redacted]" in record.stderr
    assert record.stderr.endswith("[truncated]")
    assert len(record.stderr) <= MAX_SUBPROCESS_DIAGNOSTIC_BYTES + len(" [truncated]")


def test_stream_process_stdout_drains_huge_stderr_in_bounded_chunks() -> None:
    process = FakeProcess(b"", b"x" * (64 * 1024 * 1024), 1)

    assert list(stream_process_stdout(["ffmpeg"], lambda _command: process)) == []
    assert process.stderr.read_bytes == 64 * 1024 * 1024


def test_stream_process_stdout_terminates_an_abandoned_process() -> None:
    process = FakeProcess(b"first" * (1024 * 1024))
    stream = stream_process_stdout(["ffmpeg"], lambda _command: process)

    next(stream)
    stream.close()

    assert process.terminated is True
    assert process.stdout.closed is True
    assert process.stderr.closed is True
