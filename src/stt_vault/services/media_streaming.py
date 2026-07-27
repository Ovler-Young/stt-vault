import logging
import subprocess
from collections.abc import Iterator

from stt_vault.core.process_diagnostics import (
    ProcessFactory,
    command_name,
    start_process,
    start_stderr_drain,
)

logger = logging.getLogger(__name__)


def stream_process_stdout(
    command: list[str],
    process_factory: ProcessFactory = start_process,
    *,
    asset_id: str | None = None,
) -> Iterator[bytes]:
    process = process_factory(command)
    diagnostics, stderr_thread = start_stderr_drain(process)
    try:
        if process.stdout is None:
            return
        while True:
            chunk = process.stdout.read(1024 * 1024)
            if not chunk:
                break
            yield chunk
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        if process.stdout is not None:
            process.stdout.close()
        return_code = process.wait()
        stderr_thread.join()
        if return_code:
            logger.error(
                "media streaming process failed",
                extra={
                    "event_name": "media.stream_failed",
                    "asset_id": asset_id,
                    "job_id": None,
                    "return_code": return_code,
                    "command": command_name(command),
                    "stderr": diagnostics.formatted(),
                },
            )
        if process.stderr is not None:
            process.stderr.close()
