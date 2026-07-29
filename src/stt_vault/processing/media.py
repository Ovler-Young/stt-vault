import json
import subprocess
from pathlib import Path
from typing import NotRequired, Protocol, TypedDict, Unpack, cast

from stt_vault.core.models.records import AudioStream


class CommandOptions(TypedDict, total=False):
    check: bool
    capture_output: bool
    text: bool


class CommandResult(Protocol):
    returncode: int
    stdout: str | None


class CommandRunner(Protocol):
    def __call__(self, command: list[str], **kwargs: Unpack[CommandOptions]) -> CommandResult: ...


class FfprobeFormat(TypedDict):
    duration: str | float | int


class FfprobeTags(TypedDict, total=False):
    language: NotRequired[str | None]
    title: NotRequired[str | None]


class FfprobeStream(TypedDict, total=False):
    index: NotRequired[int | None]
    codec_name: NotRequired[str | None]
    channels: NotRequired[int | None]
    channel_layout: NotRequired[str | None]
    bit_rate: NotRequired[str | None]
    tags: NotRequired[FfprobeTags]


class FfprobePayload(TypedDict, total=False):
    format: FfprobeFormat
    streams: list[FfprobeStream]


def ffprobe_duration(input_path: Path, *, runner: CommandRunner = subprocess.run) -> float:
    result = runner(
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
    payload = parse_ffprobe_payload(result.stdout)
    format_data = payload.get("format")
    if not isinstance(format_data, dict):
        raise ValueError("ffprobe response is missing a format object")
    duration = format_data.get("duration")
    if not isinstance(duration, str | int | float):
        raise ValueError("ffprobe response is missing a numeric duration")
    try:
        return float(duration)
    except ValueError as error:
        raise ValueError("ffprobe response has an invalid duration") from error


def ffprobe_audio_streams(
    input_path: Path, *, runner: CommandRunner = subprocess.run
) -> list[AudioStream]:
    result = runner(
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
    payload = parse_ffprobe_payload(result.stdout)
    raw_streams: list[FfprobeStream] = payload.get("streams", [])
    streams: list[AudioStream] = []
    for audio_index, stream in enumerate(raw_streams):
        tags = stream.get("tags", {})
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


def parse_ffprobe_payload(stdout: object) -> FfprobePayload:
    if not isinstance(stdout, str):
        raise ValueError("ffprobe did not return JSON text")
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise ValueError("ffprobe returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("ffprobe response must be a JSON object")
    raw_streams = payload.get("streams")
    if raw_streams is not None:
        if not isinstance(raw_streams, list):
            raise ValueError("ffprobe response has an invalid streams collection")
        payload["streams"] = [_parse_ffprobe_stream(stream) for stream in raw_streams]
    return cast(FfprobePayload, payload)


def _parse_ffprobe_stream(value: object) -> FfprobeStream:
    if not isinstance(value, dict):
        raise ValueError("ffprobe response contains an invalid stream")
    stream: FfprobeStream = {}
    index = value.get("index")
    if index is not None and not isinstance(index, int):
        raise ValueError("ffprobe response contains an invalid stream index")
    stream["index"] = index
    for key in ("codec_name", "channel_layout", "bit_rate"):
        field = value.get(key)
        if field is not None and not isinstance(field, str):
            raise ValueError(f"ffprobe response contains an invalid {key}")
        stream[key] = field
    channels = value.get("channels")
    if channels is not None and not isinstance(channels, int):
        raise ValueError("ffprobe response contains invalid stream channels")
    stream["channels"] = channels

    raw_tags = value.get("tags", {})
    if not isinstance(raw_tags, dict):
        raise ValueError("ffprobe response contains invalid stream tags")
    tags: FfprobeTags = {}
    for key in ("language", "title"):
        field = raw_tags.get(key)
        if field is not None and not isinstance(field, str):
            raise ValueError(f"ffprobe response contains an invalid stream tag: {key}")
        tags[key] = field
    stream["tags"] = tags
    return stream


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
