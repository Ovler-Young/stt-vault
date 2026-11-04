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


class FfprobeDisposition(TypedDict):
    attached_pic: int


class FfprobeStream(TypedDict, total=False):
    codec_type: NotRequired[str | None]
    disposition: NotRequired[FfprobeDisposition]
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


def ffprobe_media_type(input_path: Path, *, runner: CommandRunner = subprocess.run) -> str:
    result = runner(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type:stream_disposition=attached_pic",
            "-of",
            "json",
            str(input_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    has_audio = False
    for stream in parse_ffprobe_payload(result.stdout).get("streams", []):
        disposition = stream.get("disposition")
        if disposition is None:
            raise ValueError("ffprobe response is missing stream disposition")
        if stream.get("codec_type") == "video" and disposition["attached_pic"] == 0:
            return "video"
        if stream.get("codec_type") == "audio":
            has_audio = True
    if has_audio:
        return "audio"
    raise ValueError("ffprobe response contains no audio or video streams")


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
    codec_type = value.get("codec_type")
    if codec_type is not None and not isinstance(codec_type, str):
        raise ValueError("ffprobe response contains an invalid codec_type")
    stream["codec_type"] = codec_type
    raw_disposition = value.get("disposition")
    if raw_disposition is not None:
        if not isinstance(raw_disposition, dict):
            raise ValueError("ffprobe response contains an invalid stream disposition")
        attached_pic = raw_disposition.get("attached_pic")
        if type(attached_pic) is not int or attached_pic not in {0, 1}:
            raise ValueError("ffprobe response contains an invalid attached_pic disposition")
        stream["disposition"] = {"attached_pic": attached_pic}
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
