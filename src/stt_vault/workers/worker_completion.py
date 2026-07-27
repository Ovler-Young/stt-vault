import logging
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from stt_vault.core.api_models import JsonValue
from stt_vault.core.logging_config import job_log_context, log_exception_diagnostic
from stt_vault.core.settings import Settings
from stt_vault.core.types import ErrorRecord, ExportPaths, SpeakerSegment, TranscriptSegment
from stt_vault.persistence.worker_repository import SqliteWorkerRepository
from stt_vault.processing.summary_service import SummaryGenerationResult, generate_asset_summary

from .worker_failure import classify_worker_failure
from .worker_models import PreparedAsset

logger = logging.getLogger(__name__)
SummaryGenerator = Callable[[Settings, str], SummaryGenerationResult]


class CompletionRepository(Protocol):
    def mark_success(
        self,
        asset_id: str,
        *,
        wav_path: Path,
        duration: float,
        diarization_stats: dict[str, JsonValue],
        raw_segments: list[SpeakerSegment],
        merged_segments: list[SpeakerSegment],
        speaker_centroids: dict[str, list[float]],
        transcript_segments: list[TranscriptSegment],
        exports: ExportPaths,
    ) -> None: ...

    def mark_partial(self, asset_id: str, error: ErrorRecord) -> None: ...

    def add_event(
        self, asset_id: str, level: str, stage: str, message: str, payload: ErrorRecord
    ) -> None: ...


class CompletionPersistence:
    """Persist terminal job state independently from optional post-processing."""

    def __init__(
        self, settings: Settings, *, repository: CompletionRepository | None = None
    ) -> None:
        self.settings = settings
        self.repository = repository or SqliteWorkerRepository(settings.stt_db_path)

    def persist_success(
        self,
        asset_id: str,
        prepared: PreparedAsset,
        transcript_segments: list[TranscriptSegment],
        exports: ExportPaths,
    ) -> None:
        self.repository.mark_success(
            asset_id,
            wav_path=prepared.wav_path,
            duration=prepared.duration,
            diarization_stats=prepared.diarization_stats,
            raw_segments=prepared.raw_segments,
            merged_segments=prepared.merged_segments,
            speaker_centroids=prepared.speaker_centroids,
            transcript_segments=transcript_segments,
            exports=exports,
        )

    def persist_partial(
        self,
        asset_id: str,
        prepared: PreparedAsset,
        transcript_segments: list[TranscriptSegment],
        exports: ExportPaths,
        error: Exception,
    ) -> None:
        self.persist_success(asset_id, prepared, transcript_segments, exports)
        self.repository.mark_partial(asset_id, classify_worker_failure(error))


class SummaryFollowup:
    """Run automatic summaries after completion without changing terminal job state."""

    def __init__(
        self,
        settings: Settings,
        *,
        summary_generator: SummaryGenerator = generate_asset_summary,
        repository: CompletionRepository | None = None,
    ) -> None:
        self.settings = settings
        self.summary_generator = summary_generator
        self.repository = repository or SqliteWorkerRepository(settings.stt_db_path)

    def generate(self, asset_id: str) -> None:
        try:
            self.summary_generator(self.settings, asset_id)
        except Exception as error:
            log_exception_diagnostic(
                logger,
                "automatic summary generation failed",
                error,
                event_name="worker.summary_generation_failed",
                context=job_log_context(self.settings.stt_db_path, asset_id),
            )
            self.repository.add_event(
                asset_id,
                "warning",
                "summarizing content",
                "Automatic summary generation failed",
                {"category": "summary", "message": "Summary generation failed"},
            )


class CompletionStage:
    def __init__(
        self,
        settings: Settings,
        *,
        persistence: CompletionPersistence | None = None,
        summary_followup: SummaryFollowup | None = None,
        summary_generator: SummaryGenerator = generate_asset_summary,
    ) -> None:
        self.persistence = persistence or CompletionPersistence(settings)
        self.summary_followup = summary_followup or SummaryFollowup(
            settings, summary_generator=summary_generator
        )

    def complete(
        self,
        asset_id: str,
        prepared: PreparedAsset,
        transcript_segments: list[TranscriptSegment],
        exports: ExportPaths,
    ) -> None:
        self.persistence.persist_success(asset_id, prepared, transcript_segments, exports)
        self.summary_followup.generate(asset_id)

    def complete_partial(
        self,
        asset_id: str,
        prepared: PreparedAsset,
        transcript_segments: list[TranscriptSegment],
        exports: ExportPaths,
        error: Exception,
    ) -> None:
        self.persistence.persist_partial(asset_id, prepared, transcript_segments, exports, error)
