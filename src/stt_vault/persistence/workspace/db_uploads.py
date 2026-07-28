from pathlib import Path
from uuid import uuid4

from stt_vault.core.models.api import UploadSessionResponse
from stt_vault.core.models.records import UploadSessionRecord

from ..assets.db_asset_records import create_asset_from_conn
from ..shared.db_connection import connect, now, transaction


def _decode_upload_session(row: object) -> UploadSessionRecord | None:
    if row is None:
        return None
    record = UploadSessionResponse.model_validate(dict(row))
    return {
        "id": record.id,
        "filename": record.filename,
        "total_size": record.total_size,
        "offset": record.offset,
        "temp_path": record.temp_path,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def create_upload_session(
    db_path: Path,
    filename: str,
    total_size: int,
    uploads_dir: Path,
) -> UploadSessionRecord:
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
    upload = get_upload_session(db_path, upload_id)
    if upload is None:
        raise RuntimeError("created upload session was not found")
    return upload


def get_upload_session(db_path: Path, upload_id: str) -> UploadSessionRecord | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM upload_sessions WHERE id = ?",
            (upload_id,),
        ).fetchone()
    return _decode_upload_session(row)


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
