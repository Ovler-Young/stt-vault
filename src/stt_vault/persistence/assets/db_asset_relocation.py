from pathlib import Path
from typing import TypedDict

from ..folders.folder_records import get_required_folder
from ..shared.db_connection import now, transaction


class AssetNotFoundError(KeyError):
    """Raised when an asset mutation target is absent at operation time."""


class AssetMoveResult(TypedDict):
    id: str
    parent_folder_id: str | None
    updated_at: int


def move_asset(
    db_path: Path,
    asset_id: str,
    parent_folder_id: str | None,
) -> AssetMoveResult:
    timestamp = now()
    with transaction(db_path) as conn:
        asset = conn.execute(
            "SELECT id FROM assets WHERE id = ?",
            (asset_id,),
        ).fetchone()
        if asset is None:
            raise AssetNotFoundError(asset_id)
        get_required_folder(conn, parent_folder_id)
        conn.execute(
            "UPDATE assets SET parent_folder_id = ?, updated_at = ? WHERE id = ?",
            (parent_folder_id, timestamp, asset_id),
        )
    return {"id": asset_id, "parent_folder_id": parent_folder_id, "updated_at": timestamp}
