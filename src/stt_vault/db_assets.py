import json
from pathlib import Path
from typing import Any

from .db_connection import connect, now, row_to_dict, transaction
from .db_jobs import get_job, list_current_run_events, list_events
from .db_transcripts import list_transcript_chunks
from .db_visual_events import list_visual_events


def create_asset(
    db_path: Path,
    asset_id: str,
    filename: str,
    media_type: str,
    original_path: Path,
) -> None:
    timestamp = now()
    with transaction(db_path) as conn:
        conn.execute(
            """
            INSERT INTO assets (
                id, filename, media_type, original_path, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'queued', ?, ?)
            """,
            (asset_id, filename, media_type, str(original_path), timestamp, timestamp),
        )
        conn.execute(
            """
            INSERT INTO jobs (id, asset_id, status, created_at)
            VALUES (?, ?, 'queued', ?)
            """,
            (asset_id, asset_id, timestamp),
        )


def list_assets(db_path: Path) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, filename, media_type, duration, status, error, created_at, updated_at
            FROM assets
            ORDER BY created_at DESC
            """
        ).fetchall()
    return [row_to_dict(row) or {} for row in rows]


def get_asset(db_path: Path, asset_id: str) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    asset = row_to_dict(row)
    if asset is not None:
        chunks = list_transcript_chunks(db_path, asset_id)
        if chunks:
            asset["transcript_segments"] = chunks
        asset["job"] = get_job(db_path, asset_id)
        asset["events"] = list_current_run_events(db_path, asset_id)
        asset["event_history"] = list_events(db_path, asset_id)
        asset["visual_events"] = list_visual_events(db_path, asset_id)
    return asset


def update_diarization_metadata(
    db_path: Path,
    asset_id: str,
    *,
    wav_path: Path,
    duration: float,
    diarization_stats: dict[str, Any],
    raw_segments: list[dict[str, Any]],
    merged_segments: list[dict[str, Any]],
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


def update_asset_exports(db_path: Path, asset_id: str, exports: dict[str, str]) -> None:
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
) -> None:
    timestamp = now()
    with transaction(db_path) as conn:
        conn.execute(
            """
            UPDATE assets
            SET summary_status = ?, summary_text = ?, summary_error = ?, summary_model = ?,
                summary_updated_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, text, error, model, timestamp, timestamp, asset_id),
        )


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


def record_cleanup_task(
    db_path: Path, asset_id: str, media_path: Path, exports_path: Path
) -> None:
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


def get_cleanup_task(db_path: Path, asset_id: str) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT asset_id, media_path, exports_path FROM asset_cleanup_tasks WHERE asset_id = ?",
            (asset_id,),
        ).fetchone()
    return row_to_dict(row)


def clear_cleanup_task(db_path: Path, asset_id: str) -> None:
    with transaction(db_path) as conn:
        conn.execute("DELETE FROM asset_cleanup_tasks WHERE asset_id = ?", (asset_id,))
