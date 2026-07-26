import re
import subprocess
import threading
from collections.abc import Callable
from typing import Protocol

MAX_SUBPROCESS_DIAGNOSTIC_BYTES = 8 * 1024
SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?:\b|_)((?:[a-z0-9]+[_-])*?(?:api[_-]?key|password|token))\s*[=:]\s*[^\s]+|\bauthorization\s*[=:]\s*(?:Bearer\s+)?[^\s]+|\bBearer\s+[^\s]+",
    re.IGNORECASE,
)
PATH_PATTERN = re.compile(r"(?<![\w.-])(?:[A-Za-z]:)?[/\\][^\s:]+")


class BytesReader(Protocol):
    def read(self, size: int = -1) -> bytes: ...

    def close(self) -> None: ...


class Process(Protocol):
    stdout: BytesReader | None
    stderr: BytesReader | None

    def wait(self, timeout: float | None = None) -> int: ...

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


ProcessFactory = Callable[[list[str]], Process]


def format_diagnostic_text(value: str, *, truncated: bool = False) -> str:
    """Return a bounded, redacted diagnostic suitable for structured logs."""
    excerpt = value[:MAX_SUBPROCESS_DIAGNOSTIC_BYTES]
    sanitized = " ".join(excerpt.split())
    sanitized = SENSITIVE_VALUE_PATTERN.sub("[redacted]", sanitized)
    sanitized = PATH_PATTERN.sub("[path]", sanitized)
    if truncated or len(value) > MAX_SUBPROCESS_DIAGNOSTIC_BYTES:
        return f"{sanitized} [truncated]"
    return sanitized


def format_process_diagnostics(data: bytes, *, truncated: bool = False) -> str:
    """Return the only diagnostic representation allowed in logs and errors."""
    return format_diagnostic_text(data.decode(errors="replace"), truncated=truncated)


class BoundedDiagnosticCollector:
    """Retain a bounded stderr excerpt while a subprocess is still running."""

    def __init__(self, limit: int = MAX_SUBPROCESS_DIAGNOSTIC_BYTES) -> None:
        self.limit = limit
        self._data = bytearray()
        self._truncated = False

    def append(self, chunk: bytes) -> None:
        remaining = self.limit - len(self._data)
        if remaining > 0:
            self._data.extend(chunk[:remaining])
        if len(chunk) > remaining:
            self._truncated = True

    def read_from(self, stream: BytesReader, chunk_size: int = 8 * 1024) -> None:
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                return
            self.append(chunk)

    def as_bytes(self) -> bytes:
        return bytes(self._data)

    def formatted(self) -> str:
        return format_process_diagnostics(self.as_bytes(), truncated=self._truncated)


def start_process(command: list[str]) -> Process:
    return subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def start_stderr_drain(process: Process) -> tuple[BoundedDiagnosticCollector, threading.Thread]:
    diagnostics = BoundedDiagnosticCollector()

    def drain_stderr() -> None:
        if process.stderr is not None:
            diagnostics.read_from(process.stderr)

    thread = threading.Thread(target=drain_stderr, daemon=True)
    thread.start()
    return diagnostics, thread


def command_name(command: list[str]) -> str:
    return command[0] if command else "unknown"
