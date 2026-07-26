import logging
from collections.abc import Callable

from stt_vault.core.logging_config import job_log_context
from stt_vault.core.settings import Settings
from stt_vault.core.types import TranscriptSegment
from stt_vault.persistence import db
from stt_vault.processing.summary_service import SummaryGenerationResult, generate_asset_summary

from .worker_models import PreparedAsset

logger = logging.getLogger(__name__)
SummaryGenerator = Callable[[Settings, str], SummaryGenerationResult]


class CompletionPersistence:
    """Persist terminal job state independently from optional post-processing."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def persist_success(
        self,
        asset_id: str,
        prepared: PreparedAsset,
        transcript_segments: list[TranscriptSegment],
        exports: dict[str, str],
    ) -> None:
        db.mark_success(
            self.settings.stt_db_path,
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
        exports: dict[str, str],
    ) -> None:
        self.persist_success(asset_id, prepared, transcript_segments, exports)
        db.mark_partial(
            self.settings.stt_db_path,
            asset_id,
            {"category": "provider", "message": "Transcription could not complete"},
        )


class SummaryFollowup:
    """Run automatic summaries after completion without changing terminal job state."""

    def __init__(
        self, settings: Settings, *, summary_generator: SummaryGenerator = generate_asset_summary
    ) -> None:
        self.settings = settings
        self.summary_generator = summary_generator

    def generate(self, asset_id: str) -> None:
        try:
            self.summary_generator(self.settings, asset_id)
        except Exception:
            logger.exception(
                "automatic summary generation failed",
                extra={
                    **job_log_context(self.settings.stt_db_path, asset_id),
                    "event_name": "worker.summary_generation_failed",
                },
            )
            db.add_event(
                self.settings.stt_db_path,
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
        exports: dict[str, str],
    ) -> None:
        self.persistence.persist_success(asset_id, prepared, transcript_segments, exports)
        self.summary_followup.generate(asset_id)

    def complete_partial(
        self,
        asset_id: str,
        prepared: PreparedAsset,
        transcript_segments: list[TranscriptSegment],
        exports: dict[str, str],
        _error: Exception,
    ) -> None:
        self.persistence.persist_partial(asset_id, prepared, transcript_segments, exports)
