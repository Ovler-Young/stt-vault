import logging
from pathlib import Path

from . import db
from .logging_config import job_log_context
from .settings import Settings
from .types import AssetRecord, VisualEvent
from .visual import (
    detect_slide_changes,
    write_visual_event_thumbnails,
    write_visual_events_export,
)

logger = logging.getLogger(__name__)


def detect_asset_visual_events(settings: Settings, asset: AssetRecord) -> list[VisualEvent]:
    if asset.get("media_type") != "video":
        return []

    try:
        events = detect_slide_changes(
            Path(asset["original_path"]),
            sample_interval_seconds=settings.visual_sample_interval_seconds,
            threshold=settings.visual_change_threshold,
            min_gap_seconds=settings.visual_min_gap_seconds,
        )
        db.replace_visual_events(settings.stt_db_path, asset["id"], events)
        write_visual_event_thumbnails(
            Path(asset["original_path"]), settings.exports_dir, asset["id"], events
        )
        exports = dict(asset.get("exports") or {})
        exports["visual_events"] = write_visual_events_export(
            settings.exports_dir, asset["id"], events
        )
        db.update_asset_exports(settings.stt_db_path, asset["id"], exports)
        return events
    except Exception:
        logger.exception(
            "manual visual-event detection failed",
            extra={
                **job_log_context(settings.stt_db_path, asset["id"]),
                "event_name": "visual.manual_detection_failed",
                "return_code": None,
            },
        )
        raise
