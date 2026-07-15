import json
import math
import re
from dataclasses import dataclass
from typing import Any

_LOCAL_SPEAKER_PATTERN = re.compile(r"SPEAKER_\d+")
_UNUSABLE_SPEAKER_NAMES = {"unknown", "unidentified", "n/a", "none"}


@dataclass(frozen=True)
class ContentAnalysis:
    content_summary: str
    themes: list[str]
    conclusions: list[str]
    decisions: list[str]
    action_items: list[tuple[str, str | None]]
    open_questions: list[str]
    speaker_names: dict[str, str]


def build_content_analysis_prompt(
    segments: list[dict[str, Any]],
    *,
    minimum_speaker_confidence: float = 0.95,
) -> str:
    lines = []
    for segment in segments:
        speaker = str(segment.get("speaker") or "UNKNOWN")
        display_name = str(segment.get("speaker_name") or "").strip()
        label = speaker
        if display_name and display_name != speaker:
            label = f"{speaker} ({display_name})"
        start = _format_timestamp(segment.get("start"))
        end = _format_timestamp(segment.get("end"))
        text = str(segment.get("text") or "").strip()
        lines.append(f"[{label} {start}-{end}] {text}")

    return "\n".join(
        [
            "Analyze the conversation content below. Return one JSON object with exactly "
            "these keys:",
            '{"content_summary":"string","themes":["string"],"conclusions":["string"],'
            '"decisions":["string"],"action_items":[{"action":"string","owner":"string or null"}],'
            '"open_questions":["string"],"speaker_candidates":[{"speaker":"SPEAKER_XX",'
            '"name":"string","confidence":0.0}]}',
            "Summarize the subject matter, conclusions, themes, decisions, action items, and "
            "open questions. Do not describe transcription, diarization, a model, processing, or "
            "the analysis task. Use speaker_candidates only when the complete conversation "
            "provides strong evidence of a person's actual name. Do not guess. Preserve the "
            "exact SPEAKER_XX label in each candidate. Omit a candidate when confidence is "
            f"below {minimum_speaker_confidence:.2f}, when a name is not supported by the "
            "conversation, or when the speaker already has a displayed name.",
            "Conversation:",
            "\n".join(lines),
        ]
    )


def parse_content_analysis(
    content: str,
    *,
    minimum_speaker_confidence: float,
) -> ContentAnalysis:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("AI response was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("AI response was not a JSON object")

    content_summary = _required_text(payload.get("content_summary"), "content_summary")
    speaker_names: dict[str, str] = {}
    for candidate in _object_list(payload.get("speaker_candidates")):
        speaker = candidate.get("speaker")
        name = candidate.get("name")
        confidence = candidate.get("confidence")
        if not isinstance(speaker, str) or not _LOCAL_SPEAKER_PATTERN.fullmatch(speaker):
            continue
        if not _is_usable_speaker_name(name):
            continue
        if not _is_confident(confidence, minimum_speaker_confidence):
            continue
        speaker_names[speaker] = name.strip()

    action_items = []
    for item in _object_list(payload.get("action_items")):
        action = item.get("action")
        owner = item.get("owner")
        if not isinstance(action, str) or not action.strip():
            continue
        owner_value = owner.strip() if isinstance(owner, str) and owner.strip() else None
        action_items.append((action.strip(), owner_value))

    return ContentAnalysis(
        content_summary=content_summary,
        themes=_text_list(payload.get("themes")),
        conclusions=_text_list(payload.get("conclusions")),
        decisions=_text_list(payload.get("decisions")),
        action_items=action_items,
        open_questions=_text_list(payload.get("open_questions")),
        speaker_names=speaker_names,
    )


def format_content_summary(analysis: ContentAnalysis) -> str:
    sections = [analysis.content_summary]
    for heading, values in (
        ("Themes", analysis.themes),
        ("Conclusions", analysis.conclusions),
        ("Decisions", analysis.decisions),
        (
            "Action items",
            [f"{owner}: {action}" if owner else action for action, owner in analysis.action_items],
        ),
        ("Open questions", analysis.open_questions),
    ):
        if values:
            sections.append(f"{heading}\n" + "\n".join(f"- {value}" for value in values))
    return "\n\n".join(sections)


def is_local_speaker_label(value: str) -> bool:
    return bool(_LOCAL_SPEAKER_PATTERN.fullmatch(value))


def is_usable_speaker_name(value: object) -> bool:
    return _is_usable_speaker_name(value)


def _format_timestamp(value: object) -> str:
    seconds = max(0, int(float(value))) if isinstance(value, int | float) else 0
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"AI response did not include {field}")
    return value.strip()


def _text_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _object_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _is_confident(value: object, threshold: float) -> bool:
    if isinstance(value, bool):
        return False
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(confidence) and threshold <= confidence <= 1.0


def _is_usable_speaker_name(value: object) -> bool:
    if not isinstance(value, str):
        return False
    name = value.strip()
    return (
        bool(name)
        and len(name) <= 120
        and name.casefold() not in _UNUSABLE_SPEAKER_NAMES
        and not _LOCAL_SPEAKER_PATTERN.fullmatch(name)
        and all(character.isprintable() for character in name)
    )
