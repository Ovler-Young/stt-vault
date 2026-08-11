import sqlite3
import threading
from pathlib import Path

import pytest

from stt_vault.core.models.persistence_errors import MigrationStateError
from stt_vault.persistence.sqlite_database import SqliteDatabase

HISTORICAL_MIGRATIONS = (
    "H0001_assets",
    "H0002_folders",
    "H0003_speakers",
    "H0004_jobs_and_claim_columns",
    "H0005_job_events_run_attempt",
    "H0006_transcript_chunks",
    "H0007_asset_metadata_columns",
    "H0008_upload_sessions",
    "H0009_historical_indexes",
)
D0008_MIGRATIONS = (
    "D0008_001_provider_ledger_tables",
    "D0008_002_embedding_space_columns",
    "D0008_003_provider_ledger_indexes_and_triggers",
    "D0008_004_timed_transcript_units",
)


def test_initialize_schema_is_idempotent_and_creates_the_required_tables(tmp_path: Path) -> None:
    database = SqliteDatabase(tmp_path / "schema.sqlite3")
    database.initialize()
    database.initialize()

    with sqlite3.connect(tmp_path / "schema.sqlite3") as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        indexes = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
        }

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
    assert {"idx_jobs_status_created_at", "idx_provider_active_invocations"}.issubset(indexes)
    assert "transcript_timed_units" in tables

    with sqlite3.connect(tmp_path / "schema.sqlite3") as connection:
        migrations = {
            row[0] for row in connection.execute("SELECT id FROM schema_migrations ORDER BY id")
        }
    assert migrations == set(HISTORICAL_MIGRATIONS + D0008_MIGRATIONS)


def test_initialize_serializes_concurrent_schema_bootstraps(tmp_path: Path) -> None:
    database_path = tmp_path / "concurrent.sqlite3"
    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def initialize() -> None:
        try:
            barrier.wait()
            SqliteDatabase(database_path).initialize()
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=initialize) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert SqliteDatabase(database_path).list_assets() == []


@pytest.mark.parametrize("migration_id", HISTORICAL_MIGRATIONS + D0008_MIGRATIONS)
def test_initialize_rejects_a_ledgered_migration_when_its_required_schema_is_missing(
    tmp_path: Path, migration_id: str
) -> None:
    """A migration record is accepted only after its schema has been verified."""
    database_path = tmp_path / f"missing-{migration_id}.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (id TEXT PRIMARY KEY, applied_at INTEGER NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_migrations (id, applied_at) VALUES (?, ?)",
            (migration_id, 1),
        )

    with pytest.raises(MigrationStateError):
        SqliteDatabase(database_path).initialize()

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT id FROM schema_migrations").fetchall() == [
            (migration_id,)
        ]


def test_initialize_rejects_a_partial_provider_schema_without_recording_d0008_migrations(
    tmp_path: Path,
) -> None:
    """Provider adoption must not infer a missing part of an existing provider set."""
    database_path = tmp_path / "partial-provider.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (id TEXT PRIMARY KEY, applied_at INTEGER NOT NULL)"
        )
        connection.execute("CREATE TABLE provider_work_items (work_item_id TEXT PRIMARY KEY)")

    with pytest.raises(MigrationStateError):
        SqliteDatabase(database_path).initialize()

    with sqlite3.connect(database_path) as connection:
        migration_ids = {row[0] for row in connection.execute("SELECT id FROM schema_migrations")}
        assert not migration_ids.intersection(D0008_MIGRATIONS)


def test_initialize_rejects_a_recorded_migration_with_an_incompatible_table_definition(
    tmp_path: Path,
) -> None:
    """Recorded history must verify keys and types, not only column names."""
    database_path = tmp_path / "incompatible-assets.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (id TEXT PRIMARY KEY, applied_at INTEGER NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE assets (id INTEGER, filename TEXT, media_type TEXT, "
            "original_path TEXT, status TEXT, created_at INTEGER, updated_at INTEGER)"
        )
        connection.execute(
            "INSERT INTO schema_migrations (id, applied_at) VALUES (?, ?)",
            ("H0001_assets", 1),
        )

    with pytest.raises(MigrationStateError):
        SqliteDatabase(database_path).initialize()

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT id FROM schema_migrations").fetchall() == [
            ("H0001_assets",)
        ]
        assert connection.execute("PRAGMA table_info(assets)").fetchone()[5] == 0


def test_initialize_adopts_a_no_ledger_historical_schema_without_rewriting_rows(
    tmp_path: Path,
) -> None:
    """A historical database gains only additive schema and preserves durable data."""
    database_path = tmp_path / "historical.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE assets (id TEXT PRIMARY KEY, filename TEXT NOT NULL, "
            "media_type TEXT NOT NULL, original_path TEXT NOT NULL, status TEXT NOT NULL, "
            "created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)"
        )
        connection.execute(
            "INSERT INTO assets VALUES "
            "('asset-1', 'clip.wav', 'audio', '/media/clip.wav', 'queued', 1, 1)"
        )

    database = SqliteDatabase(database_path)
    database.initialize()
    database.initialize()

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT id, filename FROM assets").fetchall() == [
            ("asset-1", "clip.wav")
        ]
        assert connection.execute("SELECT id FROM schema_migrations").fetchall()
