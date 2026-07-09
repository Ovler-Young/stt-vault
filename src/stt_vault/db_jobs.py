import json
from pathlib import Path
from typing import Any

from .db_connection import connect, now, transaction


def list_jobs(db_path: Path) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                jobs.*,
                assets.filename,
                assets.media_type,
                assets.duration
            FROM jobs
            JOIN assets ON assets.id = jobs.asset_id
            ORDER BY jobs.created_at DESC
            """
        ).fetchall()
    return [_decode_job(row) for row in rows]


def get_job(db_path: Path, asset_id: str) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE asset_id = ?", (asset_id,)).fetchone()
    if row is None:
        return None
    return _decode_job(row)


def claim_next_job(db_path: Path) -> str | None:
    timestamp = now()
    with transaction(db_path) as conn:
        row = conn.execute(
            """
            SELECT id, asset_id FROM jobs
            WHERE status = 'queued'
            ORDER BY created_at ASC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            """
            UPDATE jobs
            SET status = 'processing',
                started_at = ?,
                stage = ?,
                run_attempt = run_attempt + 1
            WHERE id = ?
            """,
            (timestamp, "starting", row["id"]),
        )
        conn.execute(
            "UPDATE assets SET status = 'processing', updated_at = ? WHERE id = ?",
            (timestamp, row["asset_id"]),
        )
        return row["asset_id"]


def update_stage(db_path: Path, asset_id: str, stage: str) -> None:
    with transaction(db_path) as conn:
        conn.execute("UPDATE jobs SET stage = ? WHERE asset_id = ?", (stage, asset_id))
        conn.execute("UPDATE assets SET updated_at = ? WHERE id = ?", (now(), asset_id))
    add_event(db_path, asset_id, "info", stage, stage)


def update_progress(
    db_path: Path,
    asset_id: str,
    *,
    total_chunks: int | None = None,
    done_chunks: int | None = None,
    failed_chunks: int | None = None,
    next_retry_at: int | None = None,
) -> None:
    assignments = []
    params: list[Any] = []
    if total_chunks is not None:
        assignments.append("progress_total_chunks = ?")
        params.append(total_chunks)
    if done_chunks is not None:
        assignments.append("progress_done_chunks = ?")
        params.append(done_chunks)
    if failed_chunks is not None:
        assignments.append("progress_failed_chunks = ?")
        params.append(failed_chunks)
    assignments.append("next_retry_at = ?")
    params.append(next_retry_at)
    params.append(asset_id)
    with transaction(db_path) as conn:
        conn.execute(f"UPDATE jobs SET {', '.join(assignments)} WHERE asset_id = ?", params)


def add_event(
    db_path: Path,
    asset_id: str,
    level: str,
    stage: str | None,
    message: str,
    payload: dict[str, Any] | None = None,
) -> None:
    timestamp = now()
    with transaction(db_path) as conn:
        row = conn.execute(
            "SELECT id, run_attempt FROM jobs WHERE asset_id = ?",
            (asset_id,),
        ).fetchone()
        if row is None:
            return
        conn.execute(
            """
            INSERT INTO job_events (
                job_id, asset_id, level, stage, message, payload, run_attempt, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                asset_id,
                level,
                stage,
                message,
                json.dumps(payload) if payload is not None else None,
                row["run_attempt"],
                timestamp,
            ),
        )


def list_events(db_path: Path, asset_id: str, limit: int = 200) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, level, stage, message, payload, run_attempt, created_at
            FROM job_events
            WHERE asset_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (asset_id, limit),
        ).fetchall()
    return _decode_events(rows)


def list_current_run_events(
    db_path: Path,
    asset_id: str,
    limit: int = 200,
) -> list[dict[str, Any]]:
    job = get_job(db_path, asset_id)
    if job is None:
        return []
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, level, stage, message, payload, run_attempt, created_at
            FROM job_events
            WHERE asset_id = ? AND run_attempt = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (asset_id, job["run_attempt"], limit),
        ).fetchall()
    return _decode_events(rows)


def mark_failed(db_path: Path, asset_id: str, error: dict[str, Any]) -> None:
    payload = json.dumps(error)
    timestamp = now()
    with transaction(db_path) as conn:
        conn.execute(
            "UPDATE jobs SET status = 'failed', error = ?, finished_at = ? WHERE asset_id = ?",
            (payload, timestamp, asset_id),
        )
        conn.execute(
            "UPDATE assets SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
            (payload, timestamp, asset_id),
        )
    add_event(db_path, asset_id, "error", "failed", error.get("message", "Job failed"), error)


def mark_partial(db_path: Path, asset_id: str, error: dict[str, Any]) -> None:
    payload = json.dumps(error)
    timestamp = now()
    with transaction(db_path) as conn:
        conn.execute(
            "UPDATE jobs SET status = 'partial', error = ?, finished_at = ? WHERE asset_id = ?",
            (payload, timestamp, asset_id),
        )
        conn.execute(
            "UPDATE assets SET status = 'partial', error = ?, updated_at = ? WHERE id = ?",
            (payload, timestamp, asset_id),
        )
    add_event(
        db_path,
        asset_id,
        "error",
        "partial",
        error.get("message", "Job partially completed"),
        error,
    )


def mark_success(
    db_path: Path,
    asset_id: str,
    *,
    wav_path: Path,
    duration: float,
    diarization_stats: dict[str, Any],
    raw_segments: list[dict[str, Any]],
    merged_segments: list[dict[str, Any]],
    speaker_centroids: dict[str, list[float]],
    transcript_segments: list[dict[str, Any]],
    exports: dict[str, str],
) -> None:
    timestamp = now()
    with transaction(db_path) as conn:
        conn.execute(
            """
            UPDATE assets
            SET status = 'success',
                wav_path = ?,
                duration = ?,
                diarization_stats = ?,
                raw_segments = ?,
                merged_segments = ?,
                speaker_centroids = ?,
                transcript_segments = ?,
                exports = ?,
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
                json.dumps(transcript_segments),
                json.dumps(exports),
                timestamp,
                asset_id,
            ),
        )
        conn.execute(
            """
            UPDATE jobs
            SET status = 'success', stage = 'done', finished_at = ?
            WHERE asset_id = ?
            """,
            (timestamp, asset_id),
        )
    add_event(db_path, asset_id, "info", "done", "Job completed")


def _decode_job(row: Any) -> dict[str, Any]:
    item = dict(row)
    if item.get("error"):
        item["error"] = json.loads(item["error"])
    return item


def _decode_events(rows: list[Any]) -> list[dict[str, Any]]:
    events = []
    for row in reversed(rows):
        item = dict(row)
        if item.get("payload"):
            item["payload"] = json.loads(item["payload"])
        events.append(item)
    return events
