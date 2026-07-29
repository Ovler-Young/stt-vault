import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path

from stt_vault.core.models.api import EventResponse, JsonValue

from ..shared.db_connection import connect, decode_record, now, transaction
from .db_job_records import get_job

EVENT_JSON_FIELDS = {"payload": dict}


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
    params: list[str | int | float | bytes | None] = []
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
    payload: Mapping[str, JsonValue] | None = None,
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


def list_events(db_path: Path, asset_id: str, limit: int = 200) -> list[EventResponse]:
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
) -> list[EventResponse]:
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
            (asset_id, job.run_attempt, limit),
        ).fetchall()
    return _decode_events(rows)


def _decode_events(rows: list[sqlite3.Row]) -> list[EventResponse]:
    events: list[EventResponse] = []
    for row in reversed(rows):
        record = decode_record(row, json_fields=EVENT_JSON_FIELDS)
        if record is None:
            raise ValueError("job event record has an invalid shape")
        events.append(EventResponse.model_validate(record))
    return events
