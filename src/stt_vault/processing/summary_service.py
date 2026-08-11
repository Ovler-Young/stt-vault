from typing import Literal, Protocol, TypedDict

from openai import OpenAI

from stt_vault.core.config import Settings
from stt_vault.core.models.records import (
    AiSpeakerName,
    ApplyAiSpeakerNames,
    AssetRecord,
    AssetSummaryUpdate,
    TranscriptSegment,
)
from stt_vault.persistence.sqlite_database import SqliteDatabase

from .ai_content import (
    build_content_analysis_prompt,
    format_content_summary,
    parse_content_analysis,
)


class SummaryMessage(Protocol):
    content: str | None


class SummaryChoice(Protocol):
    message: SummaryMessage


class SummaryResponse(Protocol):
    choices: list[SummaryChoice]


class SummaryMessageParam(TypedDict):
    role: Literal["system", "user"]
    content: str


class SummaryCompletions(Protocol):
    def create(self, *, model: str, messages: list[SummaryMessageParam]) -> SummaryResponse: ...


class SummaryChat(Protocol):
    completions: SummaryCompletions


class SummaryClient(Protocol):
    chat: SummaryChat


class SummaryClientFactory(Protocol):
    def __call__(self, *, api_key: str, base_url: str) -> SummaryClient: ...


class CompletedTranscriptRequiredError(ValueError):
    pass


class SummaryGenerationResult(TypedDict):
    status: Literal["success"]
    summary: str
    title: str
    speaker_names: dict[str, str]


def require_completed_transcript(asset: AssetRecord) -> list[TranscriptSegment]:
    segments = asset.transcript_segments
    if asset.status != "success" or not segments:
        raise CompletedTranscriptRequiredError("A completed transcript is required")
    return segments


def generate_asset_summary(
    settings: Settings,
    asset_id: str,
    asset: AssetRecord | None = None,
    *,
    client_factory: SummaryClientFactory = OpenAI,
    database: SqliteDatabase,
) -> SummaryGenerationResult:
    current_asset = asset or database.get_asset(asset_id)
    if current_asset is None:
        raise KeyError(asset_id)
    segments = require_completed_transcript(current_asset)

    prompt = build_content_analysis_prompt(
        segments,
        minimum_speaker_confidence=settings.openai_speaker_name_confidence,
    )
    database.update_asset_summary(AssetSummaryUpdate(asset_id, "running"))
    try:
        client = client_factory(api_key=settings.openai_api_key, base_url=settings.openai_base_url)
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
        applied_names = database.apply_speaker_name_updates(
            ApplyAiSpeakerNames(
                asset_id,
                tuple(
                    AiSpeakerName(local_speaker, display_name)
                    for local_speaker, display_name in analysis.speaker_names.items()
                ),
            )
        )
    except Exception:
        database.update_asset_summary(
            AssetSummaryUpdate(
                asset_id,
                "failed",
                error="Summary generation failed",
                model=settings.openai_summary_model,
            )
        )
        raise

    database.update_asset_summary(
        AssetSummaryUpdate(
            asset_id,
            "success",
            text=text,
            model=settings.openai_summary_model,
            title=analysis.title,
        )
    )
    return {
        "status": "success",
        "summary": text,
        "title": analysis.title,
        "speaker_names": {name.local_speaker: name.display_name for name in applied_names.names},
    }
