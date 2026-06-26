import json
import shutil
import subprocess
import uuid
from pathlib import Path


def new_asset_id() -> str:
    return uuid.uuid4().hex[:16]


def safe_filename(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in name).strip()
    return cleaned or "upload"


def media_type_for_filename(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in {".mp4", ".mov", ".mkv", ".webm", ".mpeg", ".mpg", ".ogv"}:
        return "video"
    return "audio"


def asset_dir(data_media_dir: Path, asset_id: str) -> Path:
    return data_media_dir / asset_id


def store_upload(data_media_dir: Path, filename: str, source_path: Path) -> tuple[str, Path, str]:
    asset_id = new_asset_id()
    target_dir = asset_dir(data_media_dir, asset_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    stored_path = target_dir / safe_filename(filename)
    shutil.copyfile(source_path, stored_path)
    return asset_id, stored_path, media_type_for_filename(filename)


def ffprobe_duration(input_path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(input_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    return float(payload["format"]["duration"])


def to_wav_16k_mono(input_path: Path, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(input_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-sample_fmt",
            "s16",
            str(output_path),
        ],
        check=True,
    )
    return output_path


def extract_audio_chunk(input_wav: Path, output_path: Path, start: float, end: float) -> Path:
    duration = max(0.0, end - start)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(input_wav),
            "-ac",
            "1",
            "-ar",
            "16000",
            str(output_path),
        ],
        check=True,
    )
    return output_path

