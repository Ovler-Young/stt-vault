from pathlib import Path

from stt_vault import db
from stt_vault.db_assets import recorded_at_from_filename


def test_recorded_at_from_filename_accepts_timestamp_basename() -> None:
    assert recorded_at_from_filename("2026-07-15_12-57-52.mp4") == 1_784_120_272
    assert recorded_at_from_filename("recordings/2026-07-08_09-00-10.mp4") == 1_783_501_210
    assert recorded_at_from_filename("meeting.mp4") is None
    assert recorded_at_from_filename("2026-02-30_09-00-10.mp4") is None


def test_assets_sort_by_recorded_time_with_upload_time_fallback(tmp_path: Path) -> None:
    db_path = tmp_path / "app.sqlite3"
    db.initialize(db_path)
    db.create_asset(db_path, "older", "2026-07-08_09-00-10.mp4", "video", tmp_path / "a")
    db.create_asset(db_path, "newer", "2026-07-15_12-57-52.mp4", "video", tmp_path / "b")
    db.create_asset(db_path, "fallback", "meeting.mp4", "video", tmp_path / "c")
    with db.transaction(db_path) as conn:
        conn.execute("UPDATE assets SET created_at = ? WHERE id = ?", (1_700_000_000, "fallback"))

    assert [asset["id"] for asset in db.list_assets(db_path)] == ["newer", "older", "fallback"]
    assert [asset["id"] for asset in db.list_folder_tree(db_path)["assets"]] == [
        "newer",
        "older",
        "fallback",
    ]


def test_schema_initialization_backfills_recorded_time_for_existing_assets(tmp_path: Path) -> None:
    db_path = tmp_path / "app.sqlite3"
    db.initialize(db_path)
    db.create_asset(
        db_path,
        "existing",
        "2026-07-15_12-57-52.mp4",
        "video",
        tmp_path / "existing.mp4",
    )
    with db.transaction(db_path) as conn:
        conn.execute("UPDATE assets SET recorded_at = NULL WHERE id = 'existing'")

    db.initialize(db_path)

    asset = db.get_asset(db_path, "existing")
    assert asset is not None
    assert asset["recorded_at"] == 1_784_120_272
