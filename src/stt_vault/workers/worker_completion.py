import logging
from collections.abc import Callable

from stt_vault.core.config import Settings
from stt_vault.core.diagnostics.logging import job_log_context, log_exception_diagnostic
from stt_vault.core.models.records import (
    CompleteAsset,
    DiarizationMetadata,
    ErrorRecord,
    ExportPaths,
    JobEventCreate,
    TranscriptSegment,
)
from stt_vault.persistence.sqlite_database import SqliteDatabase
from stt_vault.processing.summary_service import SummaryGenerationResult, generate_asset_summary

from .worker_failure import classify_worker_failure
from .worker_models import PreparedAsset

logger = logging.getLogger(__name__)
SummaryGenerator = Callable[[Settings, str], SummaryGenerationResult]


class CompletionPersistence:
    """Persist terminal job state independently from optional post-processing."""

    def __init__(self, settings: Settings, database: SqliteDatabase) -> None:
        self.settings = settings
        self.database = database

    def persist_success(
        self,
        asset_id: str,
        prepared: PreparedAsset,
        transcript_segments: list[TranscriptSegment],
        exports: ExportPaths,
    ) -> None:
        self.database.complete_asset(
            CompleteAsset(
                asset_id,
                DiarizationMetadata(
                    asset_id,
                    prepared.wav_path,
                    prepared.duration,
                    prepared.diarization_stats,
                    prepared.raw_segments,
                    prepared.merged_segments,
                    prepared.speaker_centroids,
                    prepared.embedding_space,
                ),
                tuple(transcript_segments),
                exports,
            )
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
        self.database.mark_partial(asset_id, classify_worker_failure(error))


class SummaryFollowup:
    """Run automatic summaries after completion without changing terminal job state."""

    def __init__(
        self,
        settings: Settings,
        *,
        summary_generator: SummaryGenerator = generate_asset_summary,
        database: SqliteDatabase,
    ) -> None:
        self.settings = settings
        self.summary_generator = summary_generator
        self.database = database

    def generate(self, asset_id: str) -> None:
        try:
            self.summary_generator(self.settings, asset_id, database=self.database)
        except Exception as error:
            log_exception_diagnostic(
                logger,
                "automatic summary generation failed",
                error,
                event_name="worker.summary_generation_failed",
                context=job_log_context(self.database, asset_id),
            )
            self.database.add_event(
                JobEventCreate(
                    asset_id,
                    "warning",
                    "summarizing content",
                    "Automatic summary generation failed",
                    ErrorRecord("summary", "Summary generation failed"),
                )
            )


class CompletionStage:
    def __init__(
        self,
        settings: Settings,
        database: SqliteDatabase,
        *,
        persistence: CompletionPersistence | None = None,
        summary_followup: SummaryFollowup | None = None,
        summary_generator: SummaryGenerator = generate_asset_summary,
    ) -> None:
        self.persistence = persistence or CompletionPersistence(settings, database)
        self.summary_followup = summary_followup or SummaryFollowup(
            settings, database=database, summary_generator=summary_generator
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
