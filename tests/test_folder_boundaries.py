from pathlib import Path

from stt_vault.persistence.db_asset_records import create_asset
from stt_vault.persistence.db_asset_relocation import move_asset
from stt_vault.persistence.db_folders import create_folder, move_folder, rename_folder
from stt_vault.persistence.db_schema import initialize
from stt_vault.persistence.folder_tree import list_folder_tree


def test_folder_storage_mutations_persist_without_tree_projection(tmp_path: Path) -> None:
    db_path = tmp_path / "app.sqlite3"
    initialize(db_path)
    root = create_folder(db_path, "Root")
    child = create_folder(db_path, "Child", parent_id=root.id)

    renamed = rename_folder(db_path, child.id, "Renamed")
    relocated = move_folder(db_path, child.id, None)

    assert renamed.name == "Renamed"
    assert relocated.parent_id is None


def test_asset_relocation_persists_without_folder_tree_projection(tmp_path: Path) -> None:
    db_path = tmp_path / "app.sqlite3"
    initialize(db_path)
    folder = create_folder(db_path, "Meetings")
    create_asset(db_path, "asset-1", "meeting.wav", "audio", tmp_path / "meeting.wav")

    moved = move_asset(db_path, "asset-1", folder.id)

    assert moved["id"] == "asset-1"
    assert moved["parent_folder_id"] == folder.id


def test_tree_projection_assembles_persisted_folders_and_assets(tmp_path: Path) -> None:
    db_path = tmp_path / "app.sqlite3"
    initialize(db_path)
    root = create_folder(db_path, "Root")
    child = create_folder(db_path, "Child", parent_id=root.id)
    create_asset(
        db_path,
        "asset-1",
        "meeting.wav",
        "audio",
        tmp_path / "meeting.wav",
        parent_folder_id=child.id,
    )

    tree = list_folder_tree(db_path)

    assert tree.assets == []
    [tree_root] = tree.folders
    [tree_child] = tree_root.children
    assert tree_root.id == root.id
    assert tree_child.id == child.id
    assert [asset.id for asset in tree_child.assets] == ["asset-1"]
