import logging
from pathlib import Path
from typing import Protocol

from stt_vault.core.logging_config import job_log_context
from stt_vault.core.process_diagnostics import format_diagnostic_text
from stt_vault.core.settings import Settings
from stt_vault.core.types import (
    AssetRecord,
    EventPayload,
    ExportPaths,
    TranscriptSegment,
    VisualEvent,
)
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


class ExportRepository(Protocol):
    def update_stage(self, asset_id: str, stage: str) -> None: ...

    def replace_visual_events(self, asset_id: str, events: list[VisualEvent]) -> None: ...

    def add_event(
        self, asset_id: str, level: str, stage: str, message: str, payload: EventPayload
    ) -> None: ...


class SqliteExportRepository:
    def __init__(self, settings: Settings) -> None:
        self.db_path = settings.stt_db_path

    def update_stage(self, asset_id: str, stage: str) -> None:
        db.update_stage(self.db_path, asset_id, stage)

    def replace_visual_events(self, asset_id: str, events: list[VisualEvent]) -> None:
        db.replace_visual_events(self.db_path, asset_id, events)

    def add_event(
        self, asset_id: str, level: str, stage: str, message: str, payload: EventPayload
    ) -> None:
        db.add_event(self.db_path, asset_id, level, stage, message, payload)


class TranscriptExportStage:
    def __init__(self, settings: Settings, *, repository: ExportRepository | None = None) -> None:
        self.settings = settings
        self.repository = repository or SqliteExportRepository(settings)

    def write(
        self,
        asset_id: str,
        asset: AssetRecord,
        prepared: PreparedAsset,
        transcript_segments: list[TranscriptSegment],
        *,
        partial: bool,
    ) -> ExportPaths:
        self.repository.update_stage(
            asset_id, "writing partial exports" if partial else "writing exports"
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
        repository: ExportRepository | None = None,
    ) -> None:
        self.settings = settings
        self.thumbnail_runner = thumbnail_runner
        self.thumbnail_extractor = thumbnail_extractor
        self.repository = repository or SqliteExportRepository(settings)

    def detect(self, asset_id: str, asset: AssetRecord) -> ExportPaths:
        if asset.get("media_type") != "video":
            return {}
        self.repository.update_stage(asset_id, "detecting slide changes")
        try:
            events = detect_slide_changes(
                Path(asset["original_path"]),
                sample_interval_seconds=self.settings.visual_sample_interval_seconds,
                threshold=self.settings.visual_change_threshold,
                min_gap_seconds=self.settings.visual_min_gap_seconds,
            )
            self.repository.replace_visual_events(asset_id, events)
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
            self.repository.add_event(
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
