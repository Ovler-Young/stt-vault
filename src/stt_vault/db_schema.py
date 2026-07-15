import sqlite3
from pathlib import Path

from .db_connection import transaction


def initialize(db_path: Path) -> None:
    with transaction(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS assets (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                media_type TEXT NOT NULL,
                parent_folder_id TEXT REFERENCES folders(id) ON DELETE SET NULL,
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
                exports TEXT,
                summary_status TEXT,
                summary_text TEXT,
                summary_error TEXT,
                summary_model TEXT,
                summary_updated_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS folders (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                parent_id TEXT REFERENCES folders(id) ON DELETE RESTRICT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
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
                next_retry_at INTEGER,
                run_attempt INTEGER DEFAULT 0,
                claim_owner TEXT,
                claim_expires_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS job_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                level TEXT NOT NULL,
                stage TEXT,
                message TEXT NOT NULL,
                payload TEXT,
                run_attempt INTEGER DEFAULT 0,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS asset_cleanup_tasks (
                asset_id TEXT PRIMARY KEY,
                media_path TEXT NOT NULL,
                exports_path TEXT NOT NULL,
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

            CREATE TABLE IF NOT EXISTS asset_visual_events (
                asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                event_index INTEGER NOT NULL,
                timestamp REAL NOT NULL,
                score REAL NOT NULL,
                kind TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (asset_id, event_index)
            );

            CREATE INDEX IF NOT EXISTS idx_assets_created_at ON assets(created_at);
            CREATE INDEX IF NOT EXISTS idx_folders_parent_id ON folders(parent_id);
            CREATE INDEX IF NOT EXISTS idx_jobs_status_created_at ON jobs(status, created_at);
            CREATE INDEX IF NOT EXISTS idx_job_events_asset_created_at
                ON job_events(asset_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_transcript_chunks_asset_index
                ON transcript_chunks(asset_id, chunk_index);
            CREATE INDEX IF NOT EXISTS idx_visual_events_asset_index
                ON asset_visual_events(asset_id, event_index);
            """
        )
        add_missing_columns(
            conn,
            "assets",
            {
                "parent_folder_id": "TEXT REFERENCES folders(id) ON DELETE SET NULL",
                "summary_status": "TEXT",
                "summary_text": "TEXT",
                "summary_error": "TEXT",
                "summary_model": "TEXT",
                "summary_updated_at": "INTEGER",
            },
        )
        add_missing_columns(
            conn,
            "jobs",
            {
                "progress_total_chunks": "INTEGER DEFAULT 0",
                "progress_done_chunks": "INTEGER DEFAULT 0",
                "progress_failed_chunks": "INTEGER DEFAULT 0",
                "next_retry_at": "INTEGER",
                "run_attempt": "INTEGER DEFAULT 0",
                "claim_owner": "TEXT",
                "claim_expires_at": "INTEGER",
            },
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_assets_parent_folder_id ON assets(parent_folder_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_processing_claim "
            "ON jobs(status, claim_expires_at)"
        )
        add_missing_columns(
            conn,
            "job_events",
            {
                "run_attempt": "INTEGER DEFAULT 0",
            },
        )


def add_missing_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
