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


def ffprobe_audio_streams(input_path: Path) -> list[dict[str, object]]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index,codec_name,channels,channel_layout,bit_rate:stream_tags=language,title",
            "-of",
            "json",
            str(input_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    streams = []
    for audio_index, stream in enumerate(payload.get("streams") or []):
        tags = stream.get("tags") or {}
        streams.append(
            {
                "audio_index": audio_index,
                "stream_index": stream.get("index"),
                "codec_name": stream.get("codec_name"),
                "channels": stream.get("channels"),
                "channel_layout": stream.get("channel_layout"),
                "bit_rate": stream.get("bit_rate"),
                "language": tags.get("language"),
                "title": tags.get("title"),
            }
        )
    return streams


def playback_media_stream_command(input_path: Path, audio_track: str) -> list[str]:
    streams = ffprobe_audio_streams(input_path)
    if not streams:
        raise ValueError("No audio tracks are available")

    if audio_track == "all":
        pass
    else:
        try:
            track_index = int(audio_track)
        except ValueError:
            raise ValueError("audio_track must be all or an audio stream index") from None
        if track_index < 0 or track_index >= len(streams):
            raise ValueError("audio_track is out of range")

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-map",
        "0:v:0?",
    ]

    if audio_track == "all":
        if len(streams) == 1:
            command += ["-map", "0:a:0"]
        else:
            inputs = "".join(f"[0:a:{index}]" for index in range(len(streams)))
            command += [
                "-filter_complex",
                f"{inputs}amix=inputs={len(streams)}:duration=longest:normalize=0[aout]",
                "-map",
                "[aout]",
            ]
    else:
        command += ["-map", f"0:a:{int(audio_track)}"]

    command += [
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "frag_keyframe+empty_moov+default_base_moof",
        "-f",
        "mp4",
        "pipe:1",
    ]
    return command


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


def extract_transcription_chunk(
    input_media: Path,
    output_path: Path,
    start: float,
    end: float,
) -> Path:
    duration = max(0.0, end - start)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    copy_result = subprocess.run(
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
            str(input_media),
            "-vn",
            "-map",
            "0:a:0",
            "-c:a",
            "copy",
            str(output_path),
        ],
        capture_output=True,
    )
    if copy_result.returncode == 0:
        return output_path

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
            str(input_media),
            "-vn",
            "-map",
            "0:a:0",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            str(output_path),
        ],
        check=True,
    )
    return output_path
