from pathlib import Path

from stt_vault.persistence import db


def initialized_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "stt.sqlite3"
    db.initialize(db_path)
    return db_path


def create_processing_asset(tmp_path: Path, asset_id: str = "asset-1") -> Path:
    db_path = initialized_db(tmp_path)
    db.create_asset(db_path, asset_id, "clip.mp4", "video", tmp_path / "clip.mp4")
    assert db.claim_next_job(db_path) == asset_id
    return db_path
