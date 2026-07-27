import sqlite3
from pathlib import Path

from stt_vault.core.api_models import JobResponse

from .db_connection import connect, decode_record

JOB_JSON_FIELDS = {"error": dict}


def list_jobs(db_path: Path) -> list[JobResponse]:
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
    return [decode_job(row) for row in rows]


def get_job(db_path: Path, asset_id: str) -> JobResponse | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE asset_id = ?", (asset_id,)).fetchone()
    if row is None:
        return None
    return decode_job(row)


def decode_job(row: sqlite3.Row) -> JobResponse:
    record = decode_record(row, json_fields=JOB_JSON_FIELDS)
    if record is None:
        raise ValueError("job record has an invalid shape")
    return JobResponse.model_validate(record)
