import logging
from dataclasses import replace
from pathlib import Path

from stt_vault.core.config import Settings
from stt_vault.core.diagnostics.logging import job_log_context, log_exception_diagnostic
from stt_vault.core.models.records import AssetRecord, VisualEvent
from stt_vault.persistence.sqlite_database import SqliteDatabase

from .visual import (
    CommandRunner,
    ThumbnailExtractor,
    detect_slide_changes,
    run_checked_command,
    write_visual_event_thumbnails,
    write_visual_events_export,
)

logger = logging.getLogger(__name__)


def detect_asset_visual_events(
    settings: Settings,
    database: SqliteDatabase,
    asset: AssetRecord,
    *,
    thumbnail_runner: CommandRunner = run_checked_command,
    thumbnail_extractor: ThumbnailExtractor | None = None,
) -> list[VisualEvent]:
    if asset.media_type != "video":
        return []

    try:
        events = detect_slide_changes(
            Path(asset.original_path),
            sample_interval_seconds=settings.visual_sample_interval_seconds,
            threshold=settings.visual_change_threshold,
            min_gap_seconds=settings.visual_min_gap_seconds,
        )
        database.replace_visual_events(asset.id, events)
        write_visual_event_thumbnails(
            Path(asset.original_path),
            settings.exports_dir,
            asset.id,
            events,
            runner=thumbnail_runner,
            extractor=thumbnail_extractor,
        )
        exports = replace(
            asset.exports,
            visual_events=write_visual_events_export(settings.exports_dir, asset.id, events),
        )
        database.update_asset_exports(asset.id, exports)
        return events
    except Exception as error:
        log_exception_diagnostic(
            logger,
            "manual visual-event detection failed",
            error,
            event_name="visual.manual_detection_failed",
            context={
                **job_log_context(database, asset.id),
                "return_code": None,
            },
        )
        raise
