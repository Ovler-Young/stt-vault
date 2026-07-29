from pathlib import Path

from .media_probe import ffprobe_audio_streams


def playback_media_stream_command(input_path: Path, audio_track: str) -> list[str]:
    streams = ffprobe_audio_streams(input_path)
    if not streams:
        raise ValueError("No audio tracks are available")

    if audio_track != "all":
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
