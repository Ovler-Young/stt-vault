import sqlite3
from pathlib import Path
from uuid import uuid4

from stt_vault.core.api_models import FolderResponse

from .db_connection import connect, now, transaction
from .folder_records import FolderDataIntegrityError, decode_folder, get_required_folder

__all__ = ["FolderDataIntegrityError"]


def create_folder(
    db_path: Path,
    name: str,
    *,
    parent_id: str | None = None,
) -> FolderResponse:
    normalized_name = _normalize_name(name)
    folder_id = uuid4().hex
    timestamp = now()
    with transaction(db_path) as conn:
        get_required_folder(conn, parent_id)
        conn.execute(
            """
            INSERT INTO folders (id, name, parent_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (folder_id, normalized_name, parent_id, timestamp, timestamp),
        )
    return FolderResponse(
        id=folder_id,
        name=normalized_name,
        parent_id=parent_id,
        created_at=timestamp,
        updated_at=timestamp,
    )


def get_folder(db_path: Path, folder_id: str) -> FolderResponse | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, name, parent_id, created_at, updated_at FROM folders WHERE id = ?",
            (folder_id,),
        ).fetchone()
    return decode_folder(row) if row is not None else None


def list_folders(db_path: Path) -> list[FolderResponse]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, name, parent_id, created_at, updated_at
            FROM folders
            ORDER BY created_at ASC, id ASC
            """
        ).fetchall()
    return [decode_folder(row) for row in rows]


def move_folder(
    db_path: Path,
    folder_id: str,
    parent_id: str | None,
) -> FolderResponse:
    timestamp = now()
    with transaction(db_path) as conn:
        folder = get_required_folder(conn, folder_id)
        get_required_folder(conn, parent_id)
        if folder_id == parent_id:
            raise ValueError("A folder cannot be moved into itself")
        if parent_id is not None and _is_descendant(conn, folder_id, parent_id):
            raise ValueError("A folder cannot be moved into a descendant")
        conn.execute(
            "UPDATE folders SET parent_id = ?, updated_at = ? WHERE id = ?",
            (parent_id, timestamp, folder_id),
        )
    return folder.model_copy(update={"parent_id": parent_id, "updated_at": timestamp})


def rename_folder(db_path: Path, folder_id: str, name: str) -> FolderResponse:
    normalized_name = _normalize_name(name)
    timestamp = now()
    with transaction(db_path) as conn:
        folder = get_required_folder(conn, folder_id)
        conn.execute(
            "UPDATE folders SET name = ?, updated_at = ? WHERE id = ?",
            (normalized_name, timestamp, folder_id),
        )
    return folder.model_copy(update={"name": normalized_name, "updated_at": timestamp})


def delete_folder(db_path: Path, folder_id: str) -> None:
    with transaction(db_path) as conn:
        get_required_folder(conn, folder_id)
        has_child = conn.execute(
            "SELECT 1 FROM folders WHERE parent_id = ? LIMIT 1",
            (folder_id,),
        ).fetchone()
        has_asset = conn.execute(
            "SELECT 1 FROM assets WHERE parent_folder_id = ? LIMIT 1",
            (folder_id,),
        ).fetchone()
        if has_child is not None or has_asset is not None:
            raise ValueError("Folder is not empty")
        conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))


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
