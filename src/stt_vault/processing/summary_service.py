from typing import Literal, Protocol, TypedDict

from openai import OpenAI

from stt_vault.core.config import Settings
from stt_vault.core.models.records import AssetRecord, TranscriptSegment
from stt_vault.persistence.workspace.worker_repository import SqliteWorkerRepository

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


class SummaryRepository(Protocol):
    def get_asset(self, asset_id: str) -> AssetRecord | None: ...

    def update_asset_summary(
        self,
        asset_id: str,
        *,
        status: str,
        text: str | None = None,
        error: str | None = None,
        model: str | None = None,
        title: str | None = None,
    ) -> None: ...

    def apply_ai_speaker_names(
        self, asset_id: str, speaker_names: dict[str, str]
    ) -> dict[str, str]: ...


class CompletedTranscriptRequiredError(ValueError):
    pass


class SummaryGenerationResult(TypedDict):
    status: Literal["success"]
    summary: str
    title: str
    speaker_names: dict[str, str]


def require_completed_transcript(asset: AssetRecord) -> list[TranscriptSegment]:
    segments = asset.get("transcript_segments") or []
    if asset.get("status") != "success" or not segments:
        raise CompletedTranscriptRequiredError("A completed transcript is required")
    return segments


def generate_asset_summary(
    settings: Settings,
    asset_id: str,
    asset: AssetRecord | None = None,
    *,
    client_factory: SummaryClientFactory = OpenAI,
    repository: SummaryRepository | None = None,
) -> SummaryGenerationResult:
    repository = repository or SqliteWorkerRepository(settings.stt_db_path)
    current_asset = asset or repository.get_asset(asset_id)
    if current_asset is None:
        raise KeyError(asset_id)
    segments = require_completed_transcript(current_asset)

    prompt = build_content_analysis_prompt(
        segments,
        minimum_speaker_confidence=settings.openai_speaker_name_confidence,
    )
    repository.update_asset_summary(asset_id, status="running")
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
        speaker_names = repository.apply_ai_speaker_names(asset_id, analysis.speaker_names)
    except Exception:
        repository.update_asset_summary(
            asset_id,
            status="failed",
            error="Summary generation failed",
            model=settings.openai_summary_model,
        )
        raise

    repository.update_asset_summary(
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
