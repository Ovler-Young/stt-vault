from pathlib import Path

from stt_vault.core.models.records import ClaimNextJob, NewAsset
from stt_vault.persistence.sqlite_database import SqliteDatabase


def initialized_db(tmp_path: Path) -> SqliteDatabase:
    database = SqliteDatabase(tmp_path / "stt.sqlite3")
    database.initialize()
    return database


def create_processing_asset(tmp_path: Path, asset_id: str = "asset-1") -> SqliteDatabase:
    database = initialized_db(tmp_path)
    database.create_asset(NewAsset(asset_id, "clip.mp4", "video", tmp_path / "clip.mp4"))
    claim = database.claim_next_job(ClaimNextJob("test-worker", 60))
    assert claim is not None
    assert claim.asset_id == asset_id
    return database
