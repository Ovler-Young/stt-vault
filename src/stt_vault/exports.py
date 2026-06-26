import html
import json
from pathlib import Path
from typing import Any


def write_exports(
    export_dir: Path,
    asset_id: str,
    filename: str,
    transcript_segments: list[dict[str, Any]],
    raw_segments: list[dict[str, Any]],
    formats: list[str],
) -> dict[str, str]:
    target = export_dir / asset_id
    target.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, str] = {}

    if "json" in formats:
        path = target / "transcript.json"
        path.write_text(json.dumps(transcript_segments, indent=2), encoding="utf-8")
        outputs["json"] = str(path)

    if "whisper_json" in formats:
        path = target / "whisper_like.json"
        payload = {
            "text": " ".join(segment["text"] for segment in transcript_segments).strip(),
            "segments": [
                {
                    "id": index,
                    "start": segment["start"],
                    "end": segment["end"],
                    "text": segment["text"],
                    "speaker": segment["speaker"],
                    "speaker_name": segment.get("speaker_name"),
                }
                for index, segment in enumerate(transcript_segments)
            ],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        outputs["whisper_json"] = str(path)

    if "ai_text" in formats:
        path = target / "transcript.ai.txt"
        path.write_text(to_ai_text(transcript_segments), encoding="utf-8")
        outputs["ai_text"] = str(path)

    if "srt" in formats:
        path = target / "transcript.srt"
        path.write_text(to_srt(transcript_segments), encoding="utf-8")
        outputs["srt"] = str(path)

    if "vtt" in formats:
        path = target / "transcript.vtt"
        path.write_text(to_vtt(transcript_segments), encoding="utf-8")
        outputs["vtt"] = str(path)

    if "hyperaudio_html" in formats:
        path = target / "hyperaudio.html"
        path.write_text(to_hyperaudio_html(filename, transcript_segments), encoding="utf-8")
        outputs["hyperaudio_html"] = str(path)

    if "rttm" in formats:
        path = target / "speakers.rttm"
        path.write_text(to_rttm(asset_id, raw_segments), encoding="utf-8")
        outputs["rttm"] = str(path)

    return outputs


def to_srt(segments: list[dict[str, Any]]) -> str:
    blocks = []
    for index, segment in enumerate(segments, start=1):
        speaker = segment.get("speaker_name") or segment["speaker"]
        blocks.append(
            "\n".join(
                [
                    str(index),
                    f"{format_srt_time(segment['start'])} --> {format_srt_time(segment['end'])}",
                    f"{speaker}: {segment['text']}",
                ]
            )
        )
    return "\n\n".join(blocks) + "\n"


def to_ai_text(segments: list[dict[str, Any]]) -> str:
    blocks = []
    current_speaker = None
    current_lines = []

    for segment in segments:
        speaker = segment.get("speaker_name") or segment["speaker"]
        text = segment["text"].strip()
        if not text:
            continue
        if current_speaker is None:
            current_speaker = speaker
        if speaker != current_speaker:
            blocks.append(f"{current_speaker}:\n" + " ".join(current_lines))
            current_speaker = speaker
            current_lines = []
        current_lines.append(text)

    if current_speaker is not None and current_lines:
        blocks.append(f"{current_speaker}:\n" + " ".join(current_lines))

    return "\n\n".join(blocks) + "\n"


def to_vtt(segments: list[dict[str, Any]]) -> str:
    lines = ["WEBVTT", ""]
    for segment in segments:
        speaker = segment.get("speaker_name") or segment["speaker"]
        lines.extend(
            [
                f"{format_vtt_time(segment['start'])} --> {format_vtt_time(segment['end'])}",
                f"{speaker}: {segment['text']}",
                "",
            ]
        )
    return "\n".join(lines)


def to_hyperaudio_html(filename: str, segments: list[dict[str, Any]]) -> str:
    body = []
    for segment in segments:
        speaker = html.escape(segment.get("speaker_name") or segment["speaker"])
        text = html.escape(segment["text"])
        start_ms = int(float(segment["start"]) * 1000)
        duration_ms = int((float(segment["end"]) - float(segment["start"])) * 1000)
        body.append(
            f'<p><span class="speaker" data-m="{start_ms}" data-d="0">{speaker}: </span>'
            f'<span data-m="{start_ms}" data-d="{duration_ms}">{text}</span></p>'
        )
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            f"<title>{html.escape(filename)}</title>",
            "</head>",
            "<body>",
            '<article class="hyperaudio-transcript">',
            *body,
            "</article>",
            "</body>",
            "</html>",
        ]
    )


def to_rttm(asset_id: str, segments: list[dict[str, Any]]) -> str:
    lines = []
    for segment in segments:
        start = float(segment["start"])
        duration = max(0.0, float(segment["end"]) - start)
        speaker = segment["speaker"]
        lines.append(f"SPEAKER {asset_id} 1 {start:.3f} {duration:.3f} <NA> <NA> {speaker} <NA> <NA>")
    return "\n".join(lines) + "\n"


def format_srt_time(seconds: float) -> str:
    hours, remainder = divmod(float(seconds), 3600)
    minutes, remainder = divmod(remainder, 60)
    whole_seconds = int(remainder)
    millis = int(round((remainder - whole_seconds) * 1000))
    return f"{int(hours):02d}:{int(minutes):02d}:{whole_seconds:02d},{millis:03d}"


def format_vtt_time(seconds: float) -> str:
    return format_srt_time(seconds).replace(",", ".")
