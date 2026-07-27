import sqlite3
from typing import overload

from pydantic import ValidationError

from stt_vault.core.api_models import FolderAssetSummary, FolderResponse

from .db_connection import row_to_dict


class FolderDataIntegrityError(RuntimeError):
    """Raised when persisted folder data cannot form a valid response tree."""


class FolderNotFoundError(KeyError):
    """Raised when a required folder is absent at operation time."""


@overload
def get_required_folder(
    conn: sqlite3.Connection,
    folder_id: None,
) -> None: ...


@overload
def get_required_folder(
    conn: sqlite3.Connection,
    folder_id: str,
) -> FolderResponse: ...


def get_required_folder(
    conn: sqlite3.Connection,
    folder_id: str | None,
) -> FolderResponse | None:
    if folder_id is None:
        return None
    row = conn.execute(
        "SELECT id, name, parent_id, created_at, updated_at FROM folders WHERE id = ?",
        (folder_id,),
    ).fetchone()
    if row is None:
        raise FolderNotFoundError(folder_id)
    return decode_folder(row)


def decode_folder(row: sqlite3.Row) -> FolderResponse:
    record = row_to_dict(row)
    if record is None:
        raise FolderDataIntegrityError("Folder record was missing")
    try:
        return FolderResponse.model_validate(record)
    except ValidationError as exc:
        raise FolderDataIntegrityError("Folder record is invalid") from exc


def decode_folder_asset(row: sqlite3.Row) -> FolderAssetSummary:
    try:
        record = row_to_dict(row)
        if record is None:
            raise FolderDataIntegrityError("Folder asset record was missing")
        return FolderAssetSummary.model_validate(record)
    except (ValidationError, ValueError) as exc:
        raise FolderDataIntegrityError("Folder asset record is invalid") from exc
