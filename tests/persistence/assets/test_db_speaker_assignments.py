from pathlib import Path

from _support.db_assets import initialized_db


def test_speaker_assignment_operations_are_centralized(tmp_path: Path) -> None:
    database = initialized_db(tmp_path)
    for operation in (
        "delete_speaker",
        "list_asset_ids_for_speaker",
        "merge_speakers",
        "relabel_asset_speaker",
        "rename_speaker",
    ):
        assert callable(getattr(database, operation))
