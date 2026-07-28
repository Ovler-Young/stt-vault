import sqlite3
from pathlib import Path

from stt_vault.core.api_models import JsonValue
from stt_vault.core.types import CleanupTask

from .db_asset_relocation import AssetNotFoundError
from .db_connection import connect, now, row_to_dict, transaction


def record_cleanup_task(db_path: Path, asset_id: str, media_path: Path, exports_path: Path) -> None:
    with transaction(db_path) as conn:
        _upsert_cleanup_task(conn, asset_id, media_path, exports_path)


def delete_asset_with_cleanup_task(
    db_path: Path, asset_id: str, media_path: Path, exports_path: Path
) -> None:
    with transaction(db_path) as conn:
        row = conn.execute("SELECT id FROM assets WHERE id = ?", (asset_id,)).fetchone()
        if row is None:
            raise AssetNotFoundError(asset_id)
        _upsert_cleanup_task(conn, asset_id, media_path, exports_path)
        conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))


def get_cleanup_task(db_path: Path, asset_id: str) -> CleanupTask | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT asset_id, media_path, exports_path FROM asset_cleanup_tasks WHERE asset_id = ?",
            (asset_id,),
        ).fetchone()
    task = row_to_dict(row)
    if task is None:
        return None
    return CleanupTask(
        asset_id=_required_text(task, "asset_id"),
        media_path=_required_text(task, "media_path"),
        exports_path=_required_text(task, "exports_path"),
    )


def clear_cleanup_task(db_path: Path, asset_id: str) -> None:
    with transaction(db_path) as conn:
        conn.execute("DELETE FROM asset_cleanup_tasks WHERE asset_id = ?", (asset_id,))


def _upsert_cleanup_task(
    conn: sqlite3.Connection, asset_id: str, media_path: Path, exports_path: Path
) -> None:
    conn.execute(
        """
        INSERT INTO asset_cleanup_tasks (asset_id, media_path, exports_path, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(asset_id) DO UPDATE SET
            media_path = excluded.media_path,
            exports_path = excluded.exports_path
        """,
        (asset_id, str(media_path), str(exports_path), now()),
    )


def _required_text(record: dict[str, JsonValue], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value
