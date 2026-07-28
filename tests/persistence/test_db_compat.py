import sqlite3
import threading
from pathlib import Path

import pytest
from pydantic import ValidationError

from stt_vault.persistence import db
from stt_vault.persistence.shared.db_schema import ASSET_COLUMN_DEFINITIONS, ASSET_MIGRATION_COLUMNS

PUBLIC_DB_FUNCTIONS = {
    "connect",
    "transaction",
    "initialize",
    "add_missing_columns",
    "now",
    "row_to_dict",
    "create_asset",
    "create_folder",
    "delete_asset_with_cleanup_task",
    "list_assets",
    "list_folder_tree",
    "list_folders",
    "list_jobs",
    "get_job",
    "get_asset",
    "get_folder",
    "claim_next_job",
    "recover_expired_jobs",
    "renew_job_claim",
    "update_stage",
    "update_progress",
    "add_event",
    "list_events",
    "list_current_run_events",
    "mark_failed",
    "mark_partial",
    "mark_success",
    "update_diarization_metadata",
    "update_asset_exports",
    "update_asset_summary",
    "apply_ai_speaker_names",
    "retry_asset",
    "replace_visual_events",
    "list_visual_events",
    "reset_transcript_chunks",
    "upsert_transcript_chunk",
    "list_transcript_chunks",
    "list_transcript_chunks_from_conn",
    "list_speakers",
    "list_asset_ids_with_speaker_centroids",
    "get_speaker",
    "find_speaker_by_display_name",
    "upsert_speaker",
    "rename_speaker",
    "merge_speakers",
    "move_asset",
    "move_folder",
    "delete_speaker",
    "relabel_asset_speaker",
    "relabel_asset_speakers",
    "list_asset_ids_for_speaker",
    "refresh_asset_transcripts_for_speaker_from_conn",
}


def test_db_facade_preserves_public_import_surface() -> None:
    missing = [
        name for name in sorted(PUBLIC_DB_FUNCTIONS) if not callable(getattr(db, name, None))
    ]

    assert missing == []


def test_record_decoder_rejects_json_with_the_wrong_schema_type() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT '[1, 2]' AS error").fetchone()

    with pytest.raises(ValueError, match="error must decode to dict"):
        db.decode_record(row, json_fields={"error": dict})


def test_asset_decoder_rejects_malformed_persisted_segment(tmp_path: Path) -> None:
    db_path = tmp_path / "schema.sqlite3"
    db.initialize(db_path)
    db.create_asset(db_path, "asset-1", "clip.wav", "audio", tmp_path / "clip.wav")
    with db.transaction(db_path) as conn:
        conn.execute(
            "UPDATE assets SET raw_segments = ? WHERE id = ?",
            ('[{"start":"bad","end":1.0,"speaker":"SPEAKER_00"}]', "asset-1"),
        )

    with pytest.raises(ValidationError):
        db.get_asset(db_path, "asset-1")


def test_initialize_schema_is_idempotent_and_upgrades_legacy_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "schema.sqlite3"
    db.initialize(db_path)
    db.initialize(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        indexes = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        assets_columns = {row["name"] for row in conn.execute("PRAGMA table_info(assets)")}
        folders_columns = {row["name"] for row in conn.execute("PRAGMA table_info(folders)")}
        jobs_columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
        events_columns = {row["name"] for row in conn.execute("PRAGMA table_info(job_events)")}
        chunk_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(transcript_chunks)")
        }
        upload_columns = {row["name"] for row in conn.execute("PRAGMA table_info(upload_sessions)")}

    assert {
        "assets",
        "speakers",
        "jobs",
        "job_events",
        "transcript_chunks",
        "asset_visual_events",
        "folders",
        "upload_sessions",
    }.issubset(tables)
    assert {
        "idx_assets_created_at",
        "idx_jobs_status_created_at",
        "idx_job_events_asset_created_at",
        "idx_transcript_chunks_asset_index",
        "idx_visual_events_asset_index",
        "idx_assets_parent_folder_id",
        "idx_folders_parent_id",
    }.issubset(indexes)
    assert {
        "diarization_stats",
        "raw_segments",
        "merged_segments",
        "speaker_centroids",
        "transcript_segments",
        "exports",
        "parent_folder_id",
        "title",
        "recorded_at",
    }.issubset(assets_columns)
    assert {
        "id",
        "filename",
        "total_size",
        "offset",
        "temp_path",
        "created_at",
        "updated_at",
    }.issubset(upload_columns)
    assert {"id", "name", "parent_id", "created_at", "updated_at"}.issubset(folders_columns)
    assert {
        "progress_total_chunks",
        "progress_done_chunks",
        "progress_failed_chunks",
        "next_retry_at",
        "run_attempt",
    }.issubset(jobs_columns)
    assert {"run_attempt"}.issubset(events_columns)
    assert {"chunk_start", "chunk_end", "speaker_id", "speaker_name"}.issubset(chunk_columns)

    legacy_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(legacy_path) as conn:
        conn.executescript(
            """
            CREATE TABLE assets (
                id TEXT PRIMARY KEY,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY,
                asset_id TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE job_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                asset_id TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            """
        )

    db.initialize(legacy_path)

    with sqlite3.connect(legacy_path) as conn:
        conn.row_factory = sqlite3.Row
        legacy_jobs_columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
        legacy_events_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(job_events)")
        }

    assert {
        "progress_total_chunks",
        "progress_done_chunks",
        "progress_failed_chunks",
        "next_retry_at",
        "run_attempt",
    }.issubset(legacy_jobs_columns)
    assert "run_attempt" in legacy_events_columns


def test_fresh_and_migrated_asset_schema_have_the_same_columns(tmp_path: Path) -> None:
    fresh_path = tmp_path / "fresh.sqlite3"
    db.initialize(fresh_path)

    legacy_path = tmp_path / "legacy-assets.sqlite3"
    migration_names = {name for name, _definition in ASSET_MIGRATION_COLUMNS}
    legacy_definitions = [
        (name, definition)
        for name, definition in ASSET_COLUMN_DEFINITIONS
        if name not in migration_names
    ]
    with sqlite3.connect(legacy_path) as conn:
        definitions = ", ".join(f"{name} {definition}" for name, definition in legacy_definitions)
        conn.execute(f"CREATE TABLE assets ({definitions})")

    db.initialize(legacy_path)

    def asset_schema(path: Path) -> dict[str, str]:
        with sqlite3.connect(path) as conn:
            return {row[1]: row[2] for row in conn.execute("PRAGMA table_info(assets)").fetchall()}

    assert asset_schema(legacy_path) == asset_schema(fresh_path)


def test_initialize_serializes_legacy_schema_migrations(tmp_path: Path) -> None:
    legacy_path = tmp_path / "legacy-concurrent.sqlite3"
    with sqlite3.connect(legacy_path) as conn:
        conn.execute("CREATE TABLE assets (id TEXT PRIMARY KEY, created_at INTEGER NOT NULL)")
        conn.execute(
            "CREATE TABLE jobs ("
            "id TEXT PRIMARY KEY, asset_id TEXT NOT NULL, status TEXT NOT NULL, "
            "created_at INTEGER NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE job_events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, "
            "asset_id TEXT NOT NULL, created_at INTEGER NOT NULL)"
        )

    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def initialize_concurrently() -> None:
        barrier.wait()
        try:
            db.initialize(legacy_path)
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=initialize_concurrently) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    with sqlite3.connect(legacy_path) as conn:
        jobs_columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    assert {"run_attempt", "claim_owner", "claim_expires_at"}.issubset(jobs_columns)
