from pathlib import PurePosixPath
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, UploadFile

from stt_vault.core.auth import require_admin
from stt_vault.core.config import Settings
from stt_vault.core.models.api import (
    AssetBatchUploadItem,
    AssetBatchUploadResponse,
    AssetResponse,
    AssetUploadResponse,
    JobResponse,
)
from stt_vault.persistence.sqlite_database import SqliteDatabase
from stt_vault.services.asset_uploads import (
    AssetUploadDependencies,
    AssetUploadPersistenceError,
    AssetUploadTooLargeError,
    store_asset_upload,
)

from .details import asset_response, job_response

__all__ = ["register_asset_collection_routes"]


def register_asset_collection_routes(
    app: FastAPI,
    settings: Settings,
    database: SqliteDatabase,
    asset_upload_dependencies: AssetUploadDependencies,
) -> None:
    router = APIRouter()

    @router.post(
        "/api/assets",
        dependencies=[Depends(require_admin)],
        response_model=AssetUploadResponse,
    )
    async def upload_asset(file: Annotated[UploadFile, File()]) -> AssetUploadResponse:
        if not file.filename:
            raise HTTPException(status_code=400, detail="Filename is required")
        asset_id = await _store_uploaded_file(
            file, file.filename, settings, asset_upload_dependencies
        )
        return AssetUploadResponse(id=asset_id, status="queued")

    @router.post(
        "/api/assets/batch",
        dependencies=[Depends(require_admin)],
        response_model=AssetBatchUploadResponse,
        response_model_exclude_none=True,
    )
    async def upload_assets_batch(
        files: Annotated[list[UploadFile], File()],
        relative_paths: Annotated[list[str], Form()],
    ) -> AssetBatchUploadResponse:
        if len(files) != len(relative_paths):
            raise HTTPException(status_code=400, detail="Each file requires one relative path")

        results: list[AssetBatchUploadItem] = []
        for file, relative_path in zip(files, relative_paths, strict=True):
            try:
                filename = validate_relative_path(relative_path)
                asset_id = await _store_uploaded_file(
                    file, filename, settings, asset_upload_dependencies
                )
            except HTTPException as exc:
                results.append(
                    AssetBatchUploadItem(
                        path=relative_path,
                        status="failed",
                        detail=str(exc.detail),
                    )
                )
            except Exception:
                results.append(
                    AssetBatchUploadItem(
                        path=relative_path,
                        status="failed",
                        detail="Upload failed",
                    )
                )
            else:
                results.append(
                    AssetBatchUploadItem(path=relative_path, status="queued", id=asset_id)
                )
        return AssetBatchUploadResponse(results=results)

    @router.get("/api/assets")
    def list_assets(_: Annotated[None, Depends(require_admin)]) -> list[AssetResponse]:
        return [asset_response(asset) for asset in database.list_assets()]

    @router.get("/api/jobs")
    def list_jobs(_: Annotated[None, Depends(require_admin)]) -> list[JobResponse]:
        return [job_response(job) for job in database.list_jobs()]

    app.include_router(router)


def validate_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise HTTPException(status_code=400, detail="Relative path is invalid")
    return path.as_posix()


async def _store_uploaded_file(
    file: UploadFile,
    filename: str,
    settings: Settings,
    asset_upload_dependencies: AssetUploadDependencies,
) -> str:
    try:
        return await store_asset_upload(file.read, filename, settings, asset_upload_dependencies)
    except AssetUploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except AssetUploadPersistenceError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
