import shutil

from fastapi import APIRouter, Depends, FastAPI, HTTPException

from stt_vault.core.auth import require_admin
from stt_vault.core.config import Settings
from stt_vault.core.models.api import AssetDeleteResponse, AssetMoveResponse, AssetRetryResponse
from stt_vault.core.models.persistence_errors import AssetNotFoundError, FolderNotFoundError
from stt_vault.core.models.records import AssetCleanup, AssetMove
from stt_vault.core.models.requests import AssetMoveRequest
from stt_vault.persistence.sqlite_database import SqliteDatabase

__all__ = [
    "register_asset_cleanup_routes",
    "register_asset_delete_route",
    "register_asset_move_route",
    "register_asset_retry_route",
]


def register_asset_retry_route(app: FastAPI, settings: Settings, database: SqliteDatabase) -> None:
    router = APIRouter()

    @router.post(
        "/api/assets/{asset_id}/retry",
        dependencies=[Depends(require_admin)],
        response_model=AssetRetryResponse,
    )
    def retry_asset(asset_id: str) -> AssetRetryResponse:
        try:
            database.retry_asset(asset_id)
        except AssetNotFoundError:
            raise HTTPException(status_code=404, detail="Asset not found") from None
        return AssetRetryResponse(status="queued")

    app.include_router(router)


def register_asset_move_route(app: FastAPI, settings: Settings, database: SqliteDatabase) -> None:
    router = APIRouter()

    @router.post(
        "/api/assets/{asset_id}/move",
        dependencies=[Depends(require_admin)],
        response_model=AssetMoveResponse,
    )
    def move_asset(asset_id: str, payload: AssetMoveRequest) -> AssetMoveResponse:
        try:
            result = database.move_asset(AssetMove(asset_id, payload.parent_folder_id))
            return AssetMoveResponse(
                id=result.asset_id,
                parent_folder_id=result.parent_folder_id,
                updated_at=result.updated_at,
            )
        except AssetNotFoundError:
            raise HTTPException(status_code=404, detail="Asset not found") from None
        except FolderNotFoundError:
            raise HTTPException(status_code=404, detail="Folder not found") from None

    app.include_router(router)


def register_asset_delete_route(app: FastAPI, settings: Settings, database: SqliteDatabase) -> None:
    router = APIRouter()

    @router.delete(
        "/api/assets/{asset_id}",
        dependencies=[Depends(require_admin)],
        response_model=AssetDeleteResponse,
    )
    def delete_asset(asset_id: str) -> AssetDeleteResponse:
        media_path = settings.media_dir / asset_id
        exports_path = settings.exports_dir / asset_id
        try:
            database.delete_asset_with_cleanup_task(
                AssetCleanup(asset_id, media_path, exports_path)
            )
        except AssetNotFoundError:
            raise HTTPException(status_code=404, detail="Asset not found") from None
        _retry_cleanup(database, asset_id)
        return AssetDeleteResponse(status="deleted")

    app.include_router(router)


def register_asset_cleanup_routes(
    app: FastAPI, settings: Settings, database: SqliteDatabase
) -> None:
    router = APIRouter()

    @router.post(
        "/api/assets/{asset_id}/cleanup",
        dependencies=[Depends(require_admin)],
        response_model=AssetDeleteResponse,
    )
    def retry_cleanup(asset_id: str) -> AssetDeleteResponse:
        if not _retry_cleanup(database, asset_id):
            raise HTTPException(status_code=404, detail="Cleanup task not found")
        return AssetDeleteResponse(status="deleted")

    app.include_router(router)


def _retry_cleanup(database: SqliteDatabase, asset_id: str) -> bool:
    task = database.get_cleanup_task(asset_id)
    if task is None:
        return False
    for path in (task.media_path, task.exports_path):
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            continue
        except OSError:
            return False
    database.clear_cleanup_task(asset_id)
    return True
