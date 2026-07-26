import logging
from pathlib import Path

from stt_vault.core.logging_config import job_log_context
from stt_vault.core.process_diagnostics import format_diagnostic_text
from stt_vault.core.settings import Settings
from stt_vault.core.types import AssetRecord, TranscriptSegment
from stt_vault.persistence import db
from stt_vault.processing.exports import write_exports
from stt_vault.processing.visual import (
    CommandRunner,
    ThumbnailExtractor,
    detect_slide_changes,
    run_checked_command,
    write_visual_event_thumbnails,
    write_visual_events_export,
)

from .worker_models import PreparedAsset

logger = logging.getLogger(__name__)


class TranscriptExportStage:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def write(
        self,
        asset_id: str,
        asset: AssetRecord,
        prepared: PreparedAsset,
        transcript_segments: list[TranscriptSegment],
        *,
        partial: bool,
    ) -> dict[str, str]:
        db.update_stage(
            self.settings.stt_db_path,
            asset_id,
            "writing partial exports" if partial else "writing exports",
        )
        return write_exports(
            self.settings.exports_dir,
            asset_id,
            asset["filename"],
            transcript_segments,
            prepared.raw_segments,
            self.settings.parsed_export_formats,
        )


class VisualEventStage:
    def __init__(
        self,
        settings: Settings,
        *,
        thumbnail_runner: CommandRunner = run_checked_command,
        thumbnail_extractor: ThumbnailExtractor | None = None,
    ) -> None:
        self.settings = settings
        self.thumbnail_runner = thumbnail_runner
        self.thumbnail_extractor = thumbnail_extractor

    def detect(self, asset_id: str, asset: AssetRecord) -> dict[str, str]:
        if asset.get("media_type") != "video":
            return {}
        db.update_stage(self.settings.stt_db_path, asset_id, "detecting slide changes")
        try:
            events = detect_slide_changes(
                Path(asset["original_path"]),
                sample_interval_seconds=self.settings.visual_sample_interval_seconds,
                threshold=self.settings.visual_change_threshold,
                min_gap_seconds=self.settings.visual_min_gap_seconds,
            )
            db.replace_visual_events(self.settings.stt_db_path, asset_id, events)
            write_visual_event_thumbnails(
                Path(asset["original_path"]),
                self.settings.exports_dir,
                asset_id,
                events,
                runner=self.thumbnail_runner,
                extractor=self.thumbnail_extractor,
            )
            return {
                "visual_events": write_visual_events_export(
                    self.settings.exports_dir, asset_id, events
                )
            }
        except Exception as error:
            logger.exception(
                "slide-change detection failed",
                extra={
                    **job_log_context(self.settings.stt_db_path, asset_id),
                    "event_name": "worker.visual_detection_failed",
                },
            )
            db.add_event(
                self.settings.stt_db_path,
                asset_id,
                "warning",
                "detecting slide changes",
                "Slide-change detection failed",
                {
                    "category": "visual",
                    "message": "Slide-change detection failed",
                    "cause": format_diagnostic_text(str(error)),
                },
            )
            return {}
