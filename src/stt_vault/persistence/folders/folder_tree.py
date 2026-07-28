from pathlib import Path

from stt_vault.core.api_models import (
    FolderAssetSummary,
    FolderTreeNodeResponse,
    FolderTreeResponse,
)

from ..shared.db_connection import connect
from .folder_records import FolderDataIntegrityError, decode_folder, decode_folder_asset


def list_folder_tree(db_path: Path) -> FolderTreeResponse:
    with connect(db_path) as conn:
        folder_rows = conn.execute(
            """
            SELECT id, name, parent_id, created_at, updated_at
            FROM folders
            ORDER BY created_at ASC, id ASC
            """
        ).fetchall()
        asset_rows = conn.execute(
            """
            SELECT id, filename, title, recorded_at, media_type, duration, status, error,
                   summary_status, parent_folder_id, created_at, updated_at
            FROM assets
            ORDER BY COALESCE(recorded_at, created_at) DESC, created_at DESC, id DESC
            """
        ).fetchall()

    folders = [decode_folder(row) for row in folder_rows]
    by_id = {
        folder.id: FolderTreeNodeResponse(
            **folder.model_dump(),
            children=[],
            assets=[],
        )
        for folder in folders
    }
    roots: list[FolderTreeNodeResponse] = []
    for folder in folders:
        node = by_id[folder.id]
        if folder.parent_id is None:
            roots.append(node)
            continue
        parent = by_id.get(folder.parent_id)
        if parent is None:
            raise FolderDataIntegrityError("Folder references an unknown parent")
        parent.children.append(node)

    _validate_tree_reachability(roots, set(by_id))

    root_assets: list[FolderAssetSummary] = []
    for row in asset_rows:
        asset = decode_folder_asset(row)
        if asset.parent_folder_id is None:
            root_assets.append(asset)
            continue
        parent = by_id.get(asset.parent_folder_id)
        if parent is None:
            raise FolderDataIntegrityError("Asset references an unknown folder")
        parent.assets.append(asset)
    return FolderTreeResponse(folders=roots, assets=root_assets)


def _validate_tree_reachability(
    roots: list[FolderTreeNodeResponse],
    expected_ids: set[str],
) -> None:
    reachable_ids: set[str] = set()
    pending = list(roots)
    while pending:
        node = pending.pop()
        if node.id in reachable_ids:
            raise FolderDataIntegrityError("Folder appears more than once in the tree")
        reachable_ids.add(node.id)
        pending.extend(node.children)
    if reachable_ids != expected_ids:
        raise FolderDataIntegrityError("Folder tree contains a cycle or unreachable folder")
