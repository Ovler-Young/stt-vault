import json
import logging
import subprocess
from collections.abc import Callable
from pathlib import Path

from stt_vault.core.process_diagnostics import (
    ProcessFactory,
    command_name,
    start_process,
    start_stderr_drain,
)
from stt_vault.core.types import VisualEvent

FRAME_WIDTH = 32
FRAME_HEIGHT = 18
FRAME_BYTES = FRAME_WIDTH * FRAME_HEIGHT
THUMB_WIDTH = 160
THUMB_HEIGHT = 90
logger = logging.getLogger(__name__)


CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[object]]
ThumbnailExtractor = Callable[[Path, Path, float, CommandRunner], Path]


class VisualProcessingError(RuntimeError):
    """A visual-processing failure whose public representation excludes command diagnostics."""

    def __init__(self, return_code: int) -> None:
        super().__init__("ffmpeg slide-change extraction failed")
        self.return_code = return_code


def run_checked_command(command: list[str]) -> subprocess.CompletedProcess[object]:
    return subprocess.run(command, check=True)


def run_thumbnail_extractor(
    media_path: Path,
    output_path: Path,
    timestamp: float,
    runner: CommandRunner,
) -> Path:
    return extract_thumbnail(media_path, output_path, timestamp, runner=runner)


def detect_slide_changes(
    media_path: Path,
    *,
    sample_interval_seconds: float = 2.0,
    threshold: float = 18.0,
    min_gap_seconds: float = 6.0,
    process_factory: ProcessFactory = start_process,
) -> list[VisualEvent]:
    if sample_interval_seconds <= 0:
        raise ValueError("sample_interval_seconds must be positive")

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(media_path),
        "-vf",
        f"fps=1/{sample_interval_seconds},scale={FRAME_WIDTH}:{FRAME_HEIGHT},format=gray",
        "-f",
        "rawvideo",
        "pipe:1",
    ]
    process = process_factory(command)
    if process.stdout is None:
        raise RuntimeError("ffmpeg stdout pipe is unavailable")

    events: list[VisualEvent] = []
    previous: bytes | None = None
    frame_index = 0
    last_event_at = -min_gap_seconds
    diagnostics, stderr_thread = start_stderr_drain(process)
    try:
        while True:
            frame = process.stdout.read(FRAME_BYTES)
            if not frame:
                break
            if len(frame) != FRAME_BYTES:
                break

            timestamp = frame_index * sample_interval_seconds
            if previous is not None:
                score = frame_difference(previous, frame)
                if score >= threshold and timestamp - last_event_at >= min_gap_seconds:
                    events.append(
                        {
                            "timestamp": timestamp,
                            "score": score,
                            "kind": "slide_change",
                        }
                    )
                    last_event_at = timestamp

            previous = frame
            frame_index += 1
    finally:
        process.stdout.close()

    return_code = process.wait()
    stderr_thread.join()
    if process.stderr is not None:
        process.stderr.close()
    if return_code != 0:
        logger.error(
            "ffmpeg slide-change extraction failed",
            extra={
                "asset_id": media_path.stem,
                "job_id": None,
                "event_name": "visual.ffmpeg_slide_change_failed",
                "return_code": return_code,
                "command": command_name(command),
                "stderr": diagnostics.formatted(),
            },
        )
        raise VisualProcessingError(return_code)

    return events


def frame_difference(left: bytes, right: bytes) -> float:
    total = 0
    for left_value, right_value in zip(left, right, strict=False):
        total += abs(left_value - right_value)
    return total / max(1, min(len(left), len(right)))


def write_visual_events_export(
    export_dir: Path,
    asset_id: str,
    events: list[VisualEvent],
) -> str:
    target = export_dir / asset_id
    target.mkdir(parents=True, exist_ok=True)
    path = target / "visual_events.json"
    path.write_text(json.dumps(events, indent=2), encoding="utf-8")
    return str(path)


def visual_event_thumbnail_path(export_dir: Path, asset_id: str, event_index: int) -> Path:
    return export_dir / asset_id / "visual-thumbnails" / f"event-{event_index:04d}.jpg"


def write_visual_event_thumbnails(
    media_path: Path,
    export_dir: Path,
    asset_id: str,
    events: list[VisualEvent],
    *,
    runner: CommandRunner = run_checked_command,
    extractor: ThumbnailExtractor | None = None,
) -> None:
    target = export_dir / asset_id / "visual-thumbnails"
    target.mkdir(parents=True, exist_ok=True)
    extractor = extractor or run_thumbnail_extractor
    for index, event in enumerate(events):
        extractor(media_path, target / f"event-{index:04d}.jpg", float(event["timestamp"]), runner)


def extract_thumbnail(
    media_path: Path,
    output_path: Path,
    timestamp: float,
    *,
    runner: CommandRunner = run_checked_command,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    runner(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{max(0.0, timestamp):.3f}",
            "-i",
            str(media_path),
            "-frames:v",
            "1",
            "-vf",
            f"scale={THUMB_WIDTH}:{THUMB_HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={THUMB_WIDTH}:{THUMB_HEIGHT}:(ow-iw)/2:(oh-ih)/2",
            "-q:v",
            "4",
            str(output_path),
        ]
    )
    return output_path
