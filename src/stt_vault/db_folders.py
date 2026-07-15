import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

from .db_connection import connect, now, row_to_dict, transaction


def create_folder(
    db_path: Path,
    name: str,
    *,
    parent_id: str | None = None,
) -> dict[str, Any]:
    normalized_name = _normalize_name(name)
    folder_id = uuid4().hex
    timestamp = now()
    with transaction(db_path) as conn:
        _require_folder(conn, parent_id)
        conn.execute(
            """
            INSERT INTO folders (id, name, parent_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (folder_id, normalized_name, parent_id, timestamp, timestamp),
        )
    return {
        "id": folder_id,
        "name": normalized_name,
        "parent_id": parent_id,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def get_folder(db_path: Path, folder_id: str) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, name, parent_id, created_at, updated_at FROM folders WHERE id = ?",
            (folder_id,),
        ).fetchone()
    return row_to_dict(row)


def list_folders(db_path: Path) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, name, parent_id, created_at, updated_at
            FROM folders
            ORDER BY created_at ASC, id ASC
            """
        ).fetchall()
    return [row_to_dict(row) or {} for row in rows]


def list_folder_tree(db_path: Path) -> dict[str, list[dict[str, Any]]]:
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
            SELECT id, filename, media_type, duration, status, error, parent_folder_id,
                   created_at, updated_at
            FROM assets
            ORDER BY created_at DESC, id DESC
            """
        ).fetchall()

    folders = [row_to_dict(row) or {} for row in folder_rows]
    by_id = {folder["id"]: {**folder, "children": [], "assets": []} for folder in folders}
    roots: list[dict[str, Any]] = []
    for folder in folders:
        node = by_id[folder["id"]]
        parent = by_id.get(folder["parent_id"])
        if parent is None:
            roots.append(node)
        else:
            parent["children"].append(node)

    root_assets: list[dict[str, Any]] = []
    for row in asset_rows:
        asset = row_to_dict(row) or {}
        parent = by_id.get(asset["parent_folder_id"])
        if parent is None:
            root_assets.append(asset)
        else:
            parent["assets"].append(asset)
    return {"folders": roots, "assets": root_assets}


def move_folder(
    db_path: Path,
    folder_id: str,
    parent_id: str | None,
) -> dict[str, Any]:
    timestamp = now()
    with transaction(db_path) as conn:
        folder = _require_folder(conn, folder_id)
        _require_folder(conn, parent_id)
        if folder_id == parent_id:
            raise ValueError("A folder cannot be moved into itself")
        if parent_id is not None and _is_descendant(conn, folder_id, parent_id):
            raise ValueError("A folder cannot be moved into a descendant")
        conn.execute(
            "UPDATE folders SET parent_id = ?, updated_at = ? WHERE id = ?",
            (parent_id, timestamp, folder_id),
        )
    return {**folder, "parent_id": parent_id, "updated_at": timestamp}


def move_asset(
    db_path: Path,
    asset_id: str,
    parent_folder_id: str | None,
) -> dict[str, Any]:
    timestamp = now()
    with transaction(db_path) as conn:
        asset = conn.execute(
            "SELECT id FROM assets WHERE id = ?",
            (asset_id,),
        ).fetchone()
        if asset is None:
            raise KeyError(asset_id)
        _require_folder(conn, parent_folder_id)
        conn.execute(
            "UPDATE assets SET parent_folder_id = ?, updated_at = ? WHERE id = ?",
            (parent_folder_id, timestamp, asset_id),
        )
    return {"id": asset_id, "parent_folder_id": parent_folder_id, "updated_at": timestamp}


def _require_folder(
    conn: sqlite3.Connection,
    folder_id: str | None,
) -> dict[str, Any] | None:
    if folder_id is None:
        return None
    row = conn.execute(
        "SELECT id, name, parent_id, created_at, updated_at FROM folders WHERE id = ?",
        (folder_id,),
    ).fetchone()
    folder = row_to_dict(row)
    if folder is None:
        raise KeyError(folder_id)
    return folder


def _is_descendant(conn: sqlite3.Connection, folder_id: str, candidate_id: str) -> bool:
    row = conn.execute(
        """
        WITH RECURSIVE descendants(id) AS (
            SELECT id FROM folders WHERE parent_id = ?
            UNION
            SELECT folders.id
            FROM folders
            JOIN descendants ON folders.parent_id = descendants.id
        )
        SELECT 1 FROM descendants WHERE id = ?
        """,
        (folder_id, candidate_id),
    ).fetchone()
    return row is not None


def _normalize_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ValueError("Folder name is required")
    if normalized in {".", ".."} or "/" in normalized or "\\" in normalized:
        raise ValueError("Folder name cannot contain a path separator")
    if "\x00" in normalized:
        raise ValueError("Folder name cannot contain a null byte")
    return normalized
