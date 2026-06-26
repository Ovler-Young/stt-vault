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
                finished_at INTEGER,
                progress_total_chunks INTEGER DEFAULT 0,
                progress_done_chunks INTEGER DEFAULT 0,
                progress_failed_chunks INTEGER DEFAULT 0,
                next_retry_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS job_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                level TEXT NOT NULL,
                stage TEXT,
                message TEXT NOT NULL,
                payload TEXT,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS transcript_chunks (
                asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                start REAL NOT NULL,
                end REAL NOT NULL,
                chunk_start REAL NOT NULL,
                chunk_end REAL NOT NULL,
                speaker TEXT NOT NULL,
                speaker_id TEXT,
                speaker_name TEXT,
                speaker_similarity REAL,
                text TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL,
                error TEXT,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (asset_id, chunk_index)
            );

            CREATE INDEX IF NOT EXISTS idx_assets_created_at ON assets(created_at);
            CREATE INDEX IF NOT EXISTS idx_jobs_status_created_at ON jobs(status, created_at);
            CREATE INDEX IF NOT EXISTS idx_job_events_asset_created_at
                ON job_events(asset_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_transcript_chunks_asset_index
                ON transcript_chunks(asset_id, chunk_index);
            """
        )
        add_missing_columns(
            conn,
            "jobs",
            {
                "progress_total_chunks": "INTEGER DEFAULT 0",
                "progress_done_chunks": "INTEGER DEFAULT 0",
                "progress_failed_chunks": "INTEGER DEFAULT 0",
                "next_retry_at": "INTEGER",
            },
        )


def add_missing_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


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
    jobs = []
    for row in rows:
        item = dict(row)
        if item.get("error"):
            item["error"] = json.loads(item["error"])
        jobs.append(item)
    return jobs


def get_job(db_path: Path, asset_id: str) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE asset_id = ?", (asset_id,)).fetchone()
    if row is None:
        return None
    item = dict(row)
    if item.get("error"):
        item["error"] = json.loads(item["error"])
    return item


def get_asset(db_path: Path, asset_id: str) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    asset = row_to_dict(row)
    if asset is not None:
        chunks = list_transcript_chunks(db_path, asset_id)
        if chunks:
            asset["transcript_segments"] = chunks
        asset["job"] = get_job(db_path, asset_id)
        asset["events"] = list_events(db_path, asset_id)
    return asset


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
        row = conn.execute("SELECT id FROM jobs WHERE asset_id = ?", (asset_id,)).fetchone()
        if row is None:
            return
        conn.execute(
            """
            INSERT INTO job_events (job_id, asset_id, level, stage, message, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                asset_id,
                level,
                stage,
                message,
                json.dumps(payload) if payload is not None else None,
                timestamp,
            ),
        )


def list_events(db_path: Path, asset_id: str, limit: int = 200) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, level, stage, message, payload, created_at
            FROM job_events
            WHERE asset_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (asset_id, limit),
        ).fetchall()
    events = []
    for row in reversed(rows):
        item = dict(row)
        if item.get("payload"):
            item["payload"] = json.loads(item["payload"])
        events.append(item)
    return events


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
    add_event(db_path, asset_id, "error", "partial", error.get("message", "Job partially completed"), error)


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
    add_event(db_path, asset_id, "info", "done", "Job completed")


def reset_transcript_chunks(db_path: Path, asset_id: str) -> None:
    with transaction(db_path) as conn:
        conn.execute("DELETE FROM transcript_chunks WHERE asset_id = ?", (asset_id,))


def upsert_transcript_chunk(
    db_path: Path,
    asset_id: str,
    chunk_index: int,
    segment: dict[str, Any],
    *,
    attempts: int,
    status: str = "success",
    error: dict[str, Any] | None = None,
) -> None:
    timestamp = now()
    with transaction(db_path) as conn:
        conn.execute(
            """
            INSERT INTO transcript_chunks (
                asset_id,
                chunk_index,
                start,
                end,
                chunk_start,
                chunk_end,
                speaker,
                speaker_id,
                speaker_name,
                speaker_similarity,
                text,
                status,
                attempts,
                error,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(asset_id, chunk_index) DO UPDATE SET
                start = excluded.start,
                end = excluded.end,
                chunk_start = excluded.chunk_start,
                chunk_end = excluded.chunk_end,
                speaker = excluded.speaker,
                speaker_id = excluded.speaker_id,
                speaker_name = excluded.speaker_name,
                speaker_similarity = excluded.speaker_similarity,
                text = excluded.text,
                status = excluded.status,
                attempts = excluded.attempts,
                error = excluded.error,
                updated_at = excluded.updated_at
            """,
            (
                asset_id,
                chunk_index,
                segment["start"],
                segment["end"],
                segment.get("chunk_start", segment["start"]),
                segment.get("chunk_end", segment["end"]),
                segment["speaker"],
                segment.get("speaker_id"),
                segment.get("speaker_name"),
                segment.get("speaker_similarity"),
                segment["text"],
                status,
                attempts,
                json.dumps(error) if error is not None else None,
                timestamp,
            ),
        )
        conn.execute(
            "UPDATE assets SET transcript_segments = ?, updated_at = ? WHERE id = ?",
            (json.dumps(list_transcript_chunks_from_conn(conn, asset_id)), timestamp, asset_id),
        )


def list_transcript_chunks(db_path: Path, asset_id: str) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        return list_transcript_chunks_from_conn(conn, asset_id)


def list_transcript_chunks_from_conn(conn: sqlite3.Connection, asset_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM transcript_chunks
        WHERE asset_id = ?
        ORDER BY chunk_index ASC
        """,
        (asset_id,),
    ).fetchall()
    chunks = []
    for row in rows:
        item = dict(row)
        if item.get("error"):
            item["error"] = json.loads(item["error"])
        chunks.append(item)
    return chunks


def retry_asset(db_path: Path, asset_id: str) -> None:
    timestamp = now()
    with transaction(db_path) as conn:
        row = conn.execute("SELECT id FROM assets WHERE id = ?", (asset_id,)).fetchone()
        if row is None:
            raise KeyError(asset_id)
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
            SET status = 'queued',
                stage = NULL,
                error = NULL,
                started_at = NULL,
                finished_at = NULL,
                progress_total_chunks = 0,
                progress_done_chunks = 0,
                progress_failed_chunks = 0,
                next_retry_at = NULL
            WHERE asset_id = ?
            """,
            (asset_id,),
        )
    add_event(db_path, asset_id, "info", "queued", "Job queued for retry")


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
