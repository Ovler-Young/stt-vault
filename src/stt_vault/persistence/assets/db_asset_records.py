import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from stt_vault.core.api_models import AssetResponse, JsonValue
from stt_vault.core.types import AssetRecord

from ..jobs.db_job_events import list_current_run_events, list_events
from ..jobs.db_job_records import get_job
from ..shared.db_connection import connect, now, row_to_dict, transaction
from .db_transcripts import list_transcript_chunks
from .db_visual_events import list_visual_events

RECORDED_AT_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})$")


def recorded_at_from_filename(filename: str) -> int | None:
    stem = Path(filename).stem
    if not RECORDED_AT_PATTERN.fullmatch(stem):
        return None
    try:
        value = datetime.strptime(stem, "%Y-%m-%d_%H-%M-%S").replace(tzinfo=UTC)
    except ValueError:
        return None
    return int(value.timestamp())


def create_asset(
    db_path: Path,
    asset_id: str,
    filename: str,
    media_type: str,
    original_path: Path,
    *,
    parent_folder_id: str | None = None,
) -> None:
    timestamp = now()
    with transaction(db_path) as conn:
        create_asset_from_conn(
            conn,
            asset_id,
            filename,
            media_type,
            original_path,
            parent_folder_id=parent_folder_id,
            timestamp=timestamp,
        )


def create_asset_from_conn(
    conn: sqlite3.Connection,
    asset_id: str,
    filename: str,
    media_type: str,
    original_path: Path,
    *,
    parent_folder_id: str | None = None,
    timestamp: int,
) -> None:
    conn.execute(
        """
        INSERT INTO assets (
            id, filename, recorded_at, media_type, parent_folder_id, original_path, status,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)
        """,
        (
            asset_id,
            filename,
            recorded_at_from_filename(filename),
            media_type,
            parent_folder_id,
            str(original_path),
            timestamp,
            timestamp,
        ),
    )
    conn.execute(
        """
        INSERT INTO jobs (id, asset_id, status, created_at)
        VALUES (?, ?, 'queued', ?)
        """,
        (asset_id, asset_id, timestamp),
    )


def list_assets(db_path: Path) -> list[AssetRecord]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, filename, title, recorded_at, media_type, duration, status, error,
                   summary_status, parent_folder_id, created_at, updated_at
            FROM assets
            ORDER BY COALESCE(recorded_at, created_at) DESC, created_at DESC, id DESC
            """
        ).fetchall()
    return [_validated_asset(row_to_dict(row)) for row in rows]


def get_asset(
    db_path: Path,
    asset_id: str,
    *,
    include_event_history: bool = True,
) -> AssetRecord | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    asset = row_to_dict(row)
    if asset is not None:
        chunks = list_transcript_chunks(db_path, asset_id)
        if chunks:
            asset["transcript_segments"] = chunks
        job = get_job(db_path, asset_id)
        asset["job"] = job.model_dump() if job is not None else None
        asset["events"] = [
            event.model_dump() for event in list_current_run_events(db_path, asset_id)
        ]
        if include_event_history:
            asset["event_history"] = [
                event.model_dump() for event in list_events(db_path, asset_id)
            ]
        asset["visual_events"] = list_visual_events(db_path, asset_id)
    return _validated_asset(asset) if asset is not None else None


def asset_exists(db_path: Path, asset_id: str) -> bool:
    with connect(db_path) as conn:
        row = conn.execute("SELECT 1 FROM assets WHERE id = ?", (asset_id,)).fetchone()
    return row is not None


def _validated_asset(record: dict[str, JsonValue] | None) -> AssetRecord:
    if record is None:
        raise ValueError("asset record was missing")
    asset_fields = dict(record)
    asset_fields.pop("transcript_segments", None)
    AssetResponse.model_validate(asset_fields)
    return record
