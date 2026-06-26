import json
import subprocess
from pathlib import Path

FRAME_WIDTH = 32
FRAME_HEIGHT = 18
FRAME_BYTES = FRAME_WIDTH * FRAME_HEIGHT
THUMB_WIDTH = 160
THUMB_HEIGHT = 90


def detect_slide_changes(
    media_path: Path,
    *,
    sample_interval_seconds: float = 2.0,
    threshold: float = 18.0,
    min_gap_seconds: float = 6.0,
) -> list[dict[str, float | str]]:
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
    process = subprocess.Popen(command, stdout=subprocess.PIPE)
    if process.stdout is None:
        raise RuntimeError("ffmpeg stdout pipe is unavailable")

    events: list[dict[str, float | str]] = []
    previous: bytes | None = None
    frame_index = 0
    last_event_at = -min_gap_seconds
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
    if return_code != 0:
        raise subprocess.CalledProcessError(return_code, command)

    return events


def frame_difference(left: bytes, right: bytes) -> float:
    total = 0
    for left_value, right_value in zip(left, right, strict=False):
        total += abs(left_value - right_value)
    return total / max(1, min(len(left), len(right)))


def write_visual_events_export(
    export_dir: Path,
    asset_id: str,
    events: list[dict],
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
    events: list[dict],
) -> None:
    target = export_dir / asset_id / "visual-thumbnails"
    target.mkdir(parents=True, exist_ok=True)
    for index, event in enumerate(events):
        extract_thumbnail(media_path, target / f"event-{index:04d}.jpg", float(event["timestamp"]))


def extract_thumbnail(media_path: Path, output_path: Path, timestamp: float) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
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
        ],
        check=True,
    )
    return output_path
