import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from stt_vault.core.api_models import AssetResponse, JsonValue
from stt_vault.core.types import AssetRecord, CleanupTask, ExportPaths, SpeakerSegment
from stt_vault.processing.ai_content import is_local_speaker_label, is_usable_speaker_name

from .db_connection import connect, now, row_to_dict, transaction
from .db_jobs import get_job, list_current_run_events, list_events
from .db_transcripts import list_transcript_chunks, sync_asset_transcript_cache
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


def get_asset(db_path: Path, asset_id: str) -> AssetRecord | None:
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
        asset["event_history"] = [event.model_dump() for event in list_events(db_path, asset_id)]
        asset["visual_events"] = list_visual_events(db_path, asset_id)
    return _validated_asset(asset) if asset is not None else None


def update_diarization_metadata(
    db_path: Path,
    asset_id: str,
    *,
    wav_path: Path,
    duration: float,
    diarization_stats: dict[str, JsonValue],
    raw_segments: list[SpeakerSegment],
    merged_segments: list[SpeakerSegment],
    speaker_centroids: dict[str, list[float]],
) -> None:
    timestamp = now()
    with transaction(db_path) as conn:
        conn.execute(
            """
            UPDATE assets
            SET wav_path = ?,
                duration = ?,
                diarization_stats = ?,
                raw_segments = ?,
                merged_segments = ?,
                speaker_centroids = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                str(wav_path),
                duration,
                json.dumps(diarization_stats),
                json.dumps(raw_segments),
                json.dumps(merged_segments),
                json.dumps(speaker_centroids),
                timestamp,
                asset_id,
            ),
        )


def update_asset_exports(db_path: Path, asset_id: str, exports: ExportPaths) -> None:
    timestamp = now()
    with transaction(db_path) as conn:
        conn.execute(
            "UPDATE assets SET exports = ?, updated_at = ? WHERE id = ?",
            (json.dumps(exports), timestamp, asset_id),
        )


def update_asset_summary(
    db_path: Path,
    asset_id: str,
    *,
    status: str,
    text: str | None = None,
    error: str | None = None,
    model: str | None = None,
    title: str | None = None,
) -> None:
    timestamp = now()
    with transaction(db_path) as conn:
        conn.execute(
            """
            UPDATE assets
            SET summary_status = ?, summary_text = ?, summary_error = ?, summary_model = ?,
                title = COALESCE(?, title),
                summary_updated_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, text, error, model, title, timestamp, timestamp, asset_id),
        )


def apply_ai_speaker_names(
    db_path: Path,
    asset_id: str,
    speaker_names: dict[str, str],
) -> dict[str, str]:
    timestamp = now()
    applied = {}
    with transaction(db_path) as conn:
        for local_speaker, display_name in speaker_names.items():
            if not (is_local_speaker_label(local_speaker) and is_usable_speaker_name(display_name)):
                continue
            result = conn.execute(
                """
                UPDATE transcript_chunks
                SET speaker_name = ?, updated_at = ?
                WHERE asset_id = ?
                  AND speaker = ?
                  AND (speaker_name IS NULL OR trim(speaker_name) = '' OR speaker_name = speaker)
                """,
                (display_name.strip(), timestamp, asset_id, local_speaker),
            )
            if result.rowcount:
                applied[local_speaker] = display_name.strip()
        if applied:
            sync_asset_transcript_cache(conn, asset_id, timestamp)
    return applied


def retry_asset(db_path: Path, asset_id: str) -> None:
    timestamp = now()
    with transaction(db_path) as conn:
        row = conn.execute("SELECT id FROM assets WHERE id = ?", (asset_id,)).fetchone()
        if row is None:
            raise KeyError(asset_id)
        job = conn.execute(
            "SELECT id, run_attempt FROM jobs WHERE asset_id = ?",
            (asset_id,),
        ).fetchone()
        if job is None:
            raise KeyError(asset_id)
        next_run_attempt = int(job["run_attempt"]) + 1
        conn.execute(
            """
            UPDATE assets
            SET status = 'queued', error = NULL, updated_at = ?
            WHERE id = ?
            """,
            (timestamp, asset_id),
        )
        conn.execute(
            """
            UPDATE jobs
            SET status = 'queued', stage = NULL, error = NULL, started_at = NULL,
                finished_at = NULL, progress_total_chunks = 0, progress_done_chunks = 0,
                progress_failed_chunks = 0, next_retry_at = NULL, claim_owner = NULL,
                claim_expires_at = NULL
            WHERE asset_id = ?
            """,
            (asset_id,),
        )
        conn.execute(
            """
            INSERT INTO job_events (
                job_id, asset_id, level, stage, message, payload, run_attempt, created_at
            ) VALUES (?, ?, 'info', 'queued', 'Job queued for retry', NULL, ?, ?)
            """,
            (job["id"], asset_id, next_run_attempt, timestamp),
        )


def record_cleanup_task(db_path: Path, asset_id: str, media_path: Path, exports_path: Path) -> None:
    with transaction(db_path) as conn:
        conn.execute(
            """
            INSERT INTO asset_cleanup_tasks (asset_id, media_path, exports_path, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(asset_id) DO UPDATE SET
                media_path = excluded.media_path,
                exports_path = excluded.exports_path
            """,
            (asset_id, str(media_path), str(exports_path), now()),
        )


def delete_asset_with_cleanup_task(
    db_path: Path, asset_id: str, media_path: Path, exports_path: Path
) -> None:
    with transaction(db_path) as conn:
        row = conn.execute("SELECT id FROM assets WHERE id = ?", (asset_id,)).fetchone()
        if row is None:
            raise KeyError(asset_id)
        conn.execute(
            """
            INSERT INTO asset_cleanup_tasks (asset_id, media_path, exports_path, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(asset_id) DO UPDATE SET
                media_path = excluded.media_path,
                exports_path = excluded.exports_path
            """,
            (asset_id, str(media_path), str(exports_path), now()),
        )
        conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))


def get_cleanup_task(db_path: Path, asset_id: str) -> CleanupTask | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT asset_id, media_path, exports_path FROM asset_cleanup_tasks WHERE asset_id = ?",
            (asset_id,),
        ).fetchone()
    task = row_to_dict(row)
    if task is None:
        return None
    return CleanupTask(
        asset_id=_required_text(task, "asset_id"),
        media_path=_required_text(task, "media_path"),
        exports_path=_required_text(task, "exports_path"),
    )


def clear_cleanup_task(db_path: Path, asset_id: str) -> None:
    with transaction(db_path) as conn:
        conn.execute("DELETE FROM asset_cleanup_tasks WHERE asset_id = ?", (asset_id,))


def _validated_asset(record: dict[str, JsonValue] | None) -> AssetRecord:
    if record is None:
        raise ValueError("asset record was missing")
    # Transcript chunks are decoded and validated by db_transcripts. Preserve their exact
    # persisted representation here so callers do not observe Pydantic default fields.
    asset_fields = dict(record)
    asset_fields.pop("transcript_segments", None)
    AssetResponse.model_validate(asset_fields)
    return record


def _required_text(record: dict[str, JsonValue], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value
