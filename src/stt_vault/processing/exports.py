import html
import json
from dataclasses import asdict
from pathlib import Path

from stt_vault.core.models.records import ExportPaths, SpeakerSegment, TranscriptSegment


def write_exports(
    export_dir: Path,
    asset_id: str,
    filename: str,
    transcript_segments: list[TranscriptSegment],
    raw_segments: list[SpeakerSegment],
    formats: list[str],
) -> ExportPaths:
    target = export_dir / asset_id
    target.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, str] = {}

    if "json" in formats:
        path = target / "transcript.json"
        path.write_text(
            json.dumps([asdict(segment) for segment in transcript_segments], indent=2),
            encoding="utf-8",
        )
        outputs["json"] = str(path)

    if "whisper_json" in formats:
        path = target / "whisper_like.json"
        payload = {
            "text": " ".join(segment.text for segment in transcript_segments).strip(),
            "segments": [
                {
                    "id": index,
                    "start": segment.start,
                    "end": segment.end,
                    "text": segment.text,
                    "speaker": segment.speaker,
                    "speaker_name": segment.speaker_name,
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

    return ExportPaths(**outputs)


def to_srt(segments: list[TranscriptSegment]) -> str:
    blocks = []
    for index, segment in enumerate(segments, start=1):
        speaker = segment.speaker_name or segment.speaker
        blocks.append(
            "\n".join(
                [
                    str(index),
                    f"{format_srt_time(segment.start)} --> {format_srt_time(segment.end)}",
                    f"{speaker}: {segment.text}",
                ]
            )
        )
    return "\n\n".join(blocks) + "\n"


def to_ai_text(segments: list[TranscriptSegment]) -> str:
    blocks = []
    for segment in segments:
        speaker = segment.speaker_name or segment.speaker
        text = segment.text.strip()
        if not text:
            continue
        blocks.append(f"[{format_vtt_time(segment.start)}] {speaker}:\n{text}")

    return "\n\n".join(blocks) + "\n"


def to_vtt(segments: list[TranscriptSegment]) -> str:
    lines = ["WEBVTT", ""]
    for segment in segments:
        speaker = segment.speaker_name or segment.speaker
        lines.extend(
            [
                f"{format_vtt_time(segment.start)} --> {format_vtt_time(segment.end)}",
                f"{speaker}: {segment.text}",
                "",
            ]
        )
    return "\n".join(lines)


def to_hyperaudio_html(filename: str, segments: list[TranscriptSegment]) -> str:
    body = []
    for segment in segments:
        speaker = html.escape(segment.speaker_name or segment.speaker)
        text = html.escape(segment.text)
        start_ms = int(segment.start * 1000)
        duration_ms = int((segment.end - segment.start) * 1000)
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


def to_rttm(asset_id: str, segments: list[SpeakerSegment]) -> str:
    lines = []
    for segment in segments:
        start = segment.start
        duration = max(0.0, segment.end - start)
        speaker = segment.speaker
        lines.append(
            f"SPEAKER {asset_id} 1 {start:.3f} {duration:.3f} <NA> <NA> {speaker} <NA> <NA>"
        )
    return "\n".join(lines) + "\n"


def format_srt_time(seconds: float) -> str:
    hours, remainder = divmod(float(seconds), 3600)
    minutes, remainder = divmod(remainder, 60)
    whole_seconds = int(remainder)
    millis = int(round((remainder - whole_seconds) * 1000))
    return f"{int(hours):02d}:{int(minutes):02d}:{whole_seconds:02d},{millis:03d}"


def format_vtt_time(seconds: float) -> str:
    return format_srt_time(seconds).replace(",", ".")
