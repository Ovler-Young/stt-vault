from pathlib import Path

import pytest
from _support.db_assets import initialized_db

from stt_vault.core.models.records import AssetMove, FolderCreate, FolderMove, NewAsset


def test_folder_tree_and_asset_moves_preserve_a_single_hierarchy(tmp_path: Path) -> None:
    database = initialized_db(tmp_path)
    root = database.create_folder(FolderCreate("Meetings"))
    child = database.create_folder(FolderCreate("Planning", root.id))
    database.create_asset(
        NewAsset("asset-1", "roadmap.wav", "audio", tmp_path / "roadmap.wav", child.id)
    )
    database.create_asset(NewAsset("asset-2", "inbox.wav", "audio", tmp_path / "inbox.wav"))

    tree = database.list_folder_tree()

    assert [asset.id for asset in tree.assets] == ["asset-2"]
    [tree_root] = tree.folders
    assert tree_root.id == root.id
    assert tree_root.assets == []
    [tree_child] = tree_root.children
    assert tree_child.id == child.id
    assert [asset.id for asset in tree_child.assets] == ["asset-1"]

    moved_asset = database.move_asset(AssetMove("asset-2", child.id))
    moved_folder = database.move_folder(FolderMove(child.id, None))

    assert moved_asset.parent_folder_id == child.id
    assert moved_folder.parent_id is None
    moved_folder_record = database.get_folder(child.id)
    assert moved_folder_record is not None
    assert moved_folder_record.parent_id is None


def test_folder_moves_reject_missing_parents_and_descendant_cycles(tmp_path: Path) -> None:
    database = initialized_db(tmp_path)
    root = database.create_folder(FolderCreate("Root"))
    child = database.create_folder(FolderCreate("Child", root.id))
    grandchild = database.create_folder(FolderCreate("Grandchild", child.id))
    database.create_asset(NewAsset("asset-1", "clip.wav", "audio", tmp_path / "clip.wav"))

    with pytest.raises(KeyError):
        database.create_folder(FolderCreate("Missing parent", "missing"))
    with pytest.raises(KeyError):
        database.move_folder(FolderMove(child.id, "missing"))
    with pytest.raises(KeyError):
        database.move_asset(AssetMove("asset-1", "missing"))
    with pytest.raises(ValueError, match="descendant"):
        database.move_folder(FolderMove(root.id, grandchild.id))
    with pytest.raises(ValueError, match="itself"):
        database.move_folder(FolderMove(child.id, child.id))

    root_record = database.get_folder(root.id)
    child_record = database.get_folder(child.id)
    grandchild_record = database.get_folder(grandchild.id)
    assert root_record is not None
    assert child_record is not None
    assert grandchild_record is not None
    assert root_record.parent_id is None
    assert child_record.parent_id == root.id
    assert grandchild_record.parent_id == child.id
