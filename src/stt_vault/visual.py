import json
import subprocess
from pathlib import Path

FRAME_WIDTH = 32
FRAME_HEIGHT = 18
FRAME_BYTES = FRAME_WIDTH * FRAME_HEIGHT


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
