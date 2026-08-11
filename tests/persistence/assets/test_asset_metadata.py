from pathlib import Path

from _support.db_assets import initialized_db

from stt_vault.core.models.records import NewAsset


def test_recorded_at_from_filename_is_persisted_for_timestamp_basenames(tmp_path: Path) -> None:
    database = initialized_db(tmp_path)
    database.create_asset(
        NewAsset("recorded", "2026-07-15_12-57-52.mp4", "video", tmp_path / "recorded.mp4")
    )
    database.create_asset(NewAsset("plain", "meeting.mp4", "video", tmp_path / "plain.mp4"))

    recorded = database.get_asset("recorded")
    plain = database.get_asset("plain")

    assert recorded is not None
    assert plain is not None
    assert recorded.recorded_at == 1_784_120_272
    assert plain.recorded_at is None


def test_assets_and_folder_tree_sort_by_recorded_time(tmp_path: Path) -> None:
    database = initialized_db(tmp_path)
    database.create_asset(NewAsset("older", "2026-07-08_09-00-10.mp4", "video", tmp_path / "a"))
    database.create_asset(NewAsset("newer", "2026-07-15_12-57-52.mp4", "video", tmp_path / "b"))
    database.create_asset(NewAsset("fallback", "meeting.mp4", "video", tmp_path / "c"))

    assert [asset.id for asset in database.list_assets()] == ["newer", "older", "fallback"]
    assert [asset.id for asset in database.list_folder_tree().assets] == [
        "newer",
        "older",
        "fallback",
    ]
