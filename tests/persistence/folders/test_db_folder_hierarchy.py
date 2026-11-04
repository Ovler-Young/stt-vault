from pathlib import Path

import pytest
from _support.db_assets import initialized_db

from stt_vault.persistence import db


def test_folder_tree_and_asset_moves_preserve_a_single_hierarchy(tmp_path: Path) -> None:
    db_path = initialized_db(tmp_path)
    root = db.create_folder(db_path, "Meetings")
    child = db.create_folder(db_path, "Planning", parent_id=root.id)
    db.create_asset(
        db_path,
        "asset-1",
        "roadmap.wav",
        "audio",
        tmp_path / "roadmap.wav",
        parent_folder_id=child.id,
    )
    db.create_asset(
        db_path,
        "asset-2",
        "inbox.wav",
        "audio",
        tmp_path / "inbox.wav",
    )

    tree = db.list_folder_tree(db_path)

    assert [asset.id for asset in tree.assets] == ["asset-2"]
    [tree_root] = tree.folders
    assert tree_root.id == root.id
    assert tree_root.assets == []
    [tree_child] = tree_root.children
    assert tree_child.id == child.id
    assert [asset.id for asset in tree_child.assets] == ["asset-1"]

    moved_asset = db.move_asset(db_path, "asset-2", child.id)
    moved_folder = db.move_folder(db_path, child.id, None)

    assert moved_asset["parent_folder_id"] == child.id
    assert moved_folder.parent_id is None
    assert db.get_asset(db_path, "asset-2")["parent_folder_id"] == child.id
    moved_folder_record = db.get_folder(db_path, child.id)
    assert moved_folder_record is not None
    assert moved_folder_record.parent_id is None


def test_folder_moves_reject_missing_parents_and_descendant_cycles(tmp_path: Path) -> None:
    db_path = initialized_db(tmp_path)
    root = db.create_folder(db_path, "Root")
    child = db.create_folder(db_path, "Child", parent_id=root.id)
    grandchild = db.create_folder(db_path, "Grandchild", parent_id=child.id)
    db.create_asset(db_path, "asset-1", "clip.wav", "audio", tmp_path / "clip.wav")

    with pytest.raises(KeyError):
        db.create_folder(db_path, "Missing parent", parent_id="missing")
    with pytest.raises(KeyError):
        db.move_folder(db_path, child.id, "missing")
    with pytest.raises(KeyError):
        db.move_asset(db_path, "asset-1", "missing")
    with pytest.raises(ValueError, match="descendant"):
        db.move_folder(db_path, root.id, grandchild.id)
    with pytest.raises(ValueError, match="itself"):
        db.move_folder(db_path, child.id, child.id)

    root_record = db.get_folder(db_path, root.id)
    child_record = db.get_folder(db_path, child.id)
    grandchild_record = db.get_folder(db_path, grandchild.id)
    assert root_record is not None
    assert child_record is not None
    assert grandchild_record is not None
    assert root_record.parent_id is None
    assert child_record.parent_id == root.id
    assert grandchild_record.parent_id == child.id
