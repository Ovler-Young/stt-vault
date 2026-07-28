from pathlib import Path

from .db_asset_relocation import AssetNotFoundError
from .db_connection import now, transaction


def retry_asset(db_path: Path, asset_id: str) -> None:
    timestamp = now()
    with transaction(db_path) as conn:
        row = conn.execute("SELECT id FROM assets WHERE id = ?", (asset_id,)).fetchone()
        if row is None:
            raise AssetNotFoundError(asset_id)
        job = conn.execute(
            "SELECT id, run_attempt FROM jobs WHERE asset_id = ?", (asset_id,)
        ).fetchone()
        if job is None:
            raise KeyError(asset_id)
        next_run_attempt = int(job["run_attempt"]) + 1
        conn.execute(
            "UPDATE assets SET status = 'queued', error = NULL, updated_at = ? WHERE id = ?",
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
