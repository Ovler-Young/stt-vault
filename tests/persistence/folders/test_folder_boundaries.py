from pathlib import Path

from stt_vault.core.models.records import (
    AssetMove,
    FolderCreate,
    FolderMove,
    FolderRename,
    NewAsset,
)
from stt_vault.persistence.sqlite_database import SqliteDatabase


def test_folder_storage_mutations_persist_without_tree_projection(tmp_path: Path) -> None:
    database = SqliteDatabase(tmp_path / "app.sqlite3")
    database.initialize()
    root = database.create_folder(FolderCreate("Root"))
    child = database.create_folder(FolderCreate("Child", root.id))

    renamed = database.rename_folder(FolderRename(child.id, "Renamed"))
    relocated = database.move_folder(FolderMove(child.id, None))

    assert renamed.name == "Renamed"
    assert relocated.parent_id is None


def test_asset_relocation_persists_without_folder_tree_projection(tmp_path: Path) -> None:
    database = SqliteDatabase(tmp_path / "app.sqlite3")
    database.initialize()
    folder = database.create_folder(FolderCreate("Meetings"))
    database.create_asset(NewAsset("asset-1", "meeting.wav", "audio", tmp_path / "meeting.wav"))

    moved = database.move_asset(AssetMove("asset-1", folder.id))

    assert moved.asset_id == "asset-1"
    assert moved.parent_folder_id == folder.id


def test_tree_projection_assembles_persisted_folders_and_assets(tmp_path: Path) -> None:
    database = SqliteDatabase(tmp_path / "app.sqlite3")
    database.initialize()
    root = database.create_folder(FolderCreate("Root"))
    child = database.create_folder(FolderCreate("Child", root.id))
    database.create_asset(
        NewAsset("asset-1", "meeting.wav", "audio", tmp_path / "meeting.wav", child.id)
    )

    tree = database.list_folder_tree()

    assert tree.assets == []
    [tree_root] = tree.folders
    [tree_child] = tree_root.children
    assert tree_root.id == root.id
    assert tree_child.id == child.id
    assert [asset.id for asset in tree_child.assets] == ["asset-1"]
