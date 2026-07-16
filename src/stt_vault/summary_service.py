from typing import Any

from openai import OpenAI

from . import db
from .ai_content import (
    build_content_analysis_prompt,
    format_content_summary,
    parse_content_analysis,
)
from .settings import Settings


def generate_asset_summary(
    settings: Settings,
    asset_id: str,
    asset: dict[str, Any] | None = None,
) -> dict[str, object]:
    current_asset = asset or db.get_asset(settings.stt_db_path, asset_id)
    if current_asset is None:
        raise KeyError(asset_id)
    segments = current_asset.get("transcript_segments") or []
    if current_asset["status"] != "success" or not segments:
        raise ValueError("A completed transcript is required")

    prompt = build_content_analysis_prompt(
        segments,
        minimum_speaker_confidence=settings.openai_speaker_name_confidence,
    )
    db.update_asset_summary(settings.stt_db_path, asset_id, status="running")
    try:
        client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)
        response = client.chat.completions.create(
            model=settings.openai_summary_model,
            messages=[
                {
                    "role": "system",
                    "content": "Return only valid JSON. Do not add markdown fences or commentary.",
                },
                {"role": "user", "content": prompt},
            ],
        )
        analysis = parse_content_analysis(
            response.choices[0].message.content or "",
            minimum_speaker_confidence=settings.openai_speaker_name_confidence,
        )
        text = format_content_summary(analysis)
        speaker_names = db.apply_ai_speaker_names(
            settings.stt_db_path,
            asset_id,
            analysis.speaker_names,
        )
    except Exception as exc:
        db.update_asset_summary(
            settings.stt_db_path,
            asset_id,
            status="failed",
            error=str(exc),
            model=settings.openai_summary_model,
        )
        raise

    db.update_asset_summary(
        settings.stt_db_path,
        asset_id,
        status="success",
        text=text,
        model=settings.openai_summary_model,
        title=analysis.title,
    )
    return {
        "status": "success",
        "summary": text,
        "title": analysis.title,
        "speaker_names": speaker_names,
    }
