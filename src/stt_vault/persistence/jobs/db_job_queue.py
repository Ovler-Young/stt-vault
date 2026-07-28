import sqlite3
from math import isfinite
from pathlib import Path

from ..shared.db_connection import transaction


def claim_next_job(
    db_path: Path, claim_owner: str = "worker", lease_seconds: int = 120
) -> str | None:
    _validate_lease_seconds(lease_seconds)
    with transaction(db_path) as conn:
        timestamp = _database_now(conn)
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
                run_attempt = run_attempt + 1,
                claim_owner = ?,
                claim_expires_at = ?
            WHERE id = ?
            """,
            (timestamp, "starting", claim_owner, timestamp + lease_seconds, row["id"]),
        )
        conn.execute(
            "UPDATE assets SET status = 'processing', updated_at = ? WHERE id = ?",
            (timestamp, row["asset_id"]),
        )
        return row["asset_id"]


def renew_job_claim(
    db_path: Path,
    asset_id: str,
    claim_owner: str,
    lease_seconds: int,
) -> bool:
    _validate_lease_seconds(lease_seconds)
    with transaction(db_path) as conn:
        timestamp = _database_now(conn)
        result = conn.execute(
            """
            UPDATE jobs
            SET claim_expires_at = ?
            WHERE asset_id = ?
              AND status = 'processing'
              AND claim_owner = ?
              AND claim_expires_at > ?
            """,
            (timestamp + lease_seconds, asset_id, claim_owner, timestamp),
        )
    return result.rowcount == 1


def recover_expired_jobs(db_path: Path) -> list[str]:
    with transaction(db_path) as conn:
        timestamp = _database_now(conn)
        rows = conn.execute(
            """
            SELECT id, asset_id, claim_owner, claim_expires_at, run_attempt
            FROM jobs WHERE status = 'processing'
            """
        ).fetchall()
        recovered = []
        for row in rows:
            expires_at = parse_lease_expiration(row["claim_expires_at"])
            if row["claim_owner"] and expires_at is not None and expires_at > timestamp:
                continue
            conn.execute(
                """
                UPDATE jobs
                SET status = 'queued', stage = NULL, started_at = NULL,
                    claim_owner = NULL, claim_expires_at = NULL
                WHERE id = ?
                """,
                (row["id"],),
            )
            conn.execute(
                "UPDATE assets SET status = 'queued', updated_at = ? WHERE id = ?",
                (timestamp, row["asset_id"]),
            )
            conn.execute(
                """
                INSERT INTO job_events (
                    job_id, asset_id, level, stage, message, payload, run_attempt, created_at
                )
                VALUES (?, ?, 'warning', 'queued', 'Recovered interrupted job', NULL, ?, ?)
                """,
                (row["id"], row["asset_id"], row["run_attempt"], timestamp),
            )
            recovered.append(row["asset_id"])
    return recovered


def parse_lease_expiration(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, str | int | float | bytes):
        return None
    if isinstance(value, float) and (not isfinite(value) or not value.is_integer()):
        return None
    try:
        return int(value)
    except (OverflowError, ValueError):
        return None


def _validate_lease_seconds(lease_seconds: int) -> None:
    if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds < 1:
        raise ValueError("lease_seconds must be a positive integer")


def _database_now(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT unixepoch()").fetchone()
    if row is None:
        raise RuntimeError("database clock query returned no row")
    return int(row[0])
