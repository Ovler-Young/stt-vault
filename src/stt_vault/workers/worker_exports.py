import logging
from pathlib import Path

from stt_vault.core.config import Settings
from stt_vault.core.diagnostics.logging import job_log_context, log_exception_diagnostic
from stt_vault.core.diagnostics.process import format_diagnostic_text
from stt_vault.core.models.records import (
    AssetRecord,
    ErrorRecord,
    ExportPaths,
    JobEventCreate,
    TranscriptSegment,
)
from stt_vault.persistence.sqlite_database import SqliteDatabase
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
    def __init__(self, settings: Settings, database: SqliteDatabase) -> None:
        self.settings = settings
        self.database = database

    def write(
        self,
        asset_id: str,
        asset: AssetRecord,
        prepared: PreparedAsset,
        transcript_segments: list[TranscriptSegment],
        *,
        partial: bool,
    ) -> ExportPaths:
        self.database.update_stage(
            asset_id=asset_id, stage="writing partial exports" if partial else "writing exports"
        )
        return write_exports(
            self.settings.exports_dir,
            asset_id,
            asset.filename,
            transcript_segments,
            prepared.raw_segments,
            self.settings.parsed_export_formats,
        )


class VisualEventStage:
    def __init__(
        self,
        settings: Settings,
        database: SqliteDatabase,
        *,
        thumbnail_runner: CommandRunner = run_checked_command,
        thumbnail_extractor: ThumbnailExtractor | None = None,
    ) -> None:
        self.settings = settings
        self.thumbnail_runner = thumbnail_runner
        self.thumbnail_extractor = thumbnail_extractor
        self.database = database

    def detect(self, asset_id: str, asset: AssetRecord) -> ExportPaths:
        if asset.media_type != "video":
            return ExportPaths()
        self.database.update_stage(asset_id=asset_id, stage="detecting slide changes")
        try:
            events = detect_slide_changes(
                Path(asset.original_path),
                sample_interval_seconds=self.settings.visual_sample_interval_seconds,
                threshold=self.settings.visual_change_threshold,
                min_gap_seconds=self.settings.visual_min_gap_seconds,
            )
            self.database.replace_visual_events(asset_id, events)
            write_visual_event_thumbnails(
                Path(asset.original_path),
                self.settings.exports_dir,
                asset_id,
                events,
                runner=self.thumbnail_runner,
                extractor=self.thumbnail_extractor,
            )
            return ExportPaths(
                visual_events=write_visual_events_export(
                    self.settings.exports_dir, asset_id, events
                )
            )
        except Exception as error:
            log_exception_diagnostic(
                logger,
                "slide-change detection failed",
                error,
                event_name="worker.visual_detection_failed",
                context=job_log_context(self.database, asset_id),
            )
            self.database.add_event(
                JobEventCreate(
                    asset_id,
                    "warning",
                    "detecting slide changes",
                    "Slide-change detection failed",
                    ErrorRecord(
                        "visual",
                        "Slide-change detection failed",
                        format_diagnostic_text(str(error)),
                    ),
                )
            )
            return ExportPaths()
