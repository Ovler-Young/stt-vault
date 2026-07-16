from pathlib import Path
from typing import Any
from uuid import uuid4

from .db_assets import create_asset_from_conn
from .db_connection import connect, now, row_to_dict, transaction


def create_upload_session(
    db_path: Path,
    filename: str,
    total_size: int,
    uploads_dir: Path,
) -> dict[str, Any]:
    upload_id = uuid4().hex
    temp_path = uploads_dir / f"{upload_id}.part"
    timestamp = now()
    with transaction(db_path) as conn:
        conn.execute(
            """
            INSERT INTO upload_sessions (
                id, filename, total_size, offset, temp_path, created_at, updated_at
            ) VALUES (?, ?, ?, 0, ?, ?, ?)
            """,
            (upload_id, filename, total_size, str(temp_path), timestamp, timestamp),
        )
    return get_upload_session(db_path, upload_id) or {}


def get_upload_session(db_path: Path, upload_id: str) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM upload_sessions WHERE id = ?",
            (upload_id,),
        ).fetchone()
    return row_to_dict(row)


def update_upload_offset(db_path: Path, upload_id: str, offset: int) -> None:
    with transaction(db_path) as conn:
        conn.execute(
            "UPDATE upload_sessions SET offset = ?, updated_at = ? WHERE id = ?",
            (offset, now(), upload_id),
        )


def delete_upload_session(db_path: Path, upload_id: str) -> None:
    with transaction(db_path) as conn:
        conn.execute("DELETE FROM upload_sessions WHERE id = ?", (upload_id,))


def complete_upload_session(
    db_path: Path,
    upload_id: str,
    asset_id: str,
    media_type: str,
    stored_path: Path,
) -> None:
    with transaction(db_path) as conn:
        upload = conn.execute(
            "SELECT filename FROM upload_sessions WHERE id = ?",
            (upload_id,),
        ).fetchone()
        if upload is None:
            raise KeyError(upload_id)
        create_asset_from_conn(
            conn,
            asset_id,
            upload["filename"],
            media_type,
            stored_path,
            timestamp=now(),
        )
        conn.execute("DELETE FROM upload_sessions WHERE id = ?", (upload_id,))
