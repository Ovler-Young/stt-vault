import logging
from collections.abc import Callable

from . import db
from .logging_config import job_log_context
from .settings import Settings
from .summary_service import SummaryGenerationResult, generate_asset_summary
from .types import TranscriptSegment
from .worker_models import PreparedAsset

logger = logging.getLogger(__name__)
SummaryGenerator = Callable[[Settings, str], SummaryGenerationResult]


class CompletionStage:
    def __init__(
        self, settings: Settings, *, summary_generator: SummaryGenerator = generate_asset_summary
    ) -> None:
        self.settings = settings
        self.summary_generator = summary_generator

    def complete(
        self,
        asset_id: str,
        prepared: PreparedAsset,
        transcript_segments: list[TranscriptSegment],
        exports: dict[str, str],
    ) -> None:
        db.update_asset_summary(self.settings.stt_db_path, asset_id, status="running")
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

    def complete_partial(
        self,
        asset_id: str,
        prepared: PreparedAsset,
        transcript_segments: list[TranscriptSegment],
        exports: dict[str, str],
        _error: Exception,
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
        db.mark_partial(
            self.settings.stt_db_path,
            asset_id,
            {"category": "provider", "message": "Transcription could not complete"},
        )
