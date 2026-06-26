import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def transaction(db_path: Path):
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def initialize(db_path: Path) -> None:
    with transaction(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS assets (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                media_type TEXT NOT NULL,
                original_path TEXT NOT NULL,
                wav_path TEXT,
                duration REAL,
                status TEXT NOT NULL,
                error TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                diarization_stats TEXT,
                raw_segments TEXT,
                merged_segments TEXT,
                speaker_centroids TEXT,
                transcript_segments TEXT,
                exports TEXT
            );

            CREATE TABLE IF NOT EXISTS speakers (
                id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                centroid TEXT NOT NULL,
                sample_count INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                status TEXT NOT NULL,
                stage TEXT,
                error TEXT,
                created_at INTEGER NOT NULL,
                started_at INTEGER,
                finished_at INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_assets_created_at ON assets(created_at);
            CREATE INDEX IF NOT EXISTS idx_jobs_status_created_at ON jobs(status, created_at);
            """
        )


def now() -> int:
    return int(time.time())


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    for key in (
        "diarization_stats",
        "raw_segments",
        "merged_segments",
        "speaker_centroids",
        "transcript_segments",
        "exports",
        "error",
    ):
        if data.get(key):
            data[key] = json.loads(data[key])
    return data


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
    return row_to_dict(row)


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
            "UPDATE jobs SET status = 'processing', started_at = ?, stage = ? WHERE id = ?",
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
            "UPDATE jobs SET status = 'success', stage = 'done', finished_at = ? WHERE asset_id = ?",
            (timestamp, asset_id),
        )


def list_speakers(db_path: Path) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM speakers ORDER BY display_name").fetchall()
    speakers = []
    for row in rows:
        item = dict(row)
        item["centroid"] = json.loads(item["centroid"])
        speakers.append(item)
    return speakers


def upsert_speaker(
    db_path: Path,
    speaker_id: str,
    display_name: str,
    centroid: list[float],
    sample_count: int,
) -> None:
    timestamp = now()
    with transaction(db_path) as conn:
        conn.execute(
            """
            INSERT INTO speakers (id, display_name, centroid, sample_count, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                display_name = excluded.display_name,
                centroid = excluded.centroid,
                sample_count = excluded.sample_count,
                updated_at = excluded.updated_at
            """,
            (speaker_id, display_name, json.dumps(centroid), sample_count, timestamp, timestamp),
        )

