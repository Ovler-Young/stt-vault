import subprocess
from pathlib import Path

from .media_probe import CommandRunner


def to_wav_16k_mono(
    input_path: Path, output_path: Path, *, runner: CommandRunner = subprocess.run
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    runner(
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


def extract_audio_chunk(
    input_wav: Path,
    output_path: Path,
    start: float,
    end: float,
    *,
    runner: CommandRunner = subprocess.run,
) -> Path:
    duration = max(0.0, end - start)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    runner(
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
    *,
    runner: CommandRunner = subprocess.run,
) -> Path:
    duration = max(0.0, end - start)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    copy_result = runner(
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

    runner(
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
