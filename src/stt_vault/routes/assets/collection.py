import logging
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, UploadFile

from stt_vault.core.auth import require_admin
from stt_vault.core.config import Settings
from stt_vault.core.diagnostics.logging import log_exception_diagnostic
from stt_vault.core.models.api import (
    AssetBatchUploadItem,
    AssetBatchUploadResponse,
    AssetResponse,
    AssetUploadResponse,
    JobResponse,
)
from stt_vault.persistence import db
from stt_vault.services.media_storage import store_upload

__all__ = ["register_asset_collection_routes"]
logger = logging.getLogger(__name__)


def register_asset_collection_routes(app: FastAPI, settings: Settings) -> None:
    router = APIRouter()

    @router.post(
        "/api/assets",
        dependencies=[Depends(require_admin)],
        response_model=AssetUploadResponse,
    )
    async def upload_asset(file: Annotated[UploadFile, File()]) -> AssetUploadResponse:
        if not file.filename:
            raise HTTPException(status_code=400, detail="Filename is required")
        asset_id = await _store_uploaded_file(file, file.filename, settings)
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
                asset_id = await _store_uploaded_file(file, filename, settings)
            except HTTPException as exc:
                results.append(
                    AssetBatchUploadItem(
                        path=relative_path,
                        status="failed",
                        detail=str(exc.detail),
                    )
                )
            except Exception as exc:
                log_exception_diagnostic(
                    logger,
                    "batch upload failed",
                    exc,
                    event_name="upload.batch_item_failed",
                    context={"upload_path": relative_path},
                )
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
        return db.list_assets(settings.stt_db_path)

    @router.get("/api/jobs")
    def list_jobs(_: Annotated[None, Depends(require_admin)]) -> list[JobResponse]:
        return db.list_jobs(settings.stt_db_path)

    app.include_router(router)


async def _store_uploaded_file(file: UploadFile, filename: str, settings: Settings) -> str:
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = Path(tmp.name)
        copied = 0
        max_bytes = settings.max_upload_bytes
        while chunk := await file.read(1024 * 1024):
            copied += len(chunk)
            if copied > max_bytes:
                tmp_path.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="Upload is too large")
            tmp.write(chunk)
    try:
        asset_id, stored_path, media_type = store_upload(settings.media_dir, filename, tmp_path)
        try:
            db.create_asset(settings.stt_db_path, asset_id, filename, media_type, stored_path)
        except Exception:
            shutil.rmtree(settings.media_dir / asset_id, ignore_errors=True)
            raise
        return asset_id
    except HTTPException:
        raise
    except Exception as exc:
        log_exception_diagnostic(
            logger,
            "upload persistence failed",
            exc,
            event_name="upload.persistence_failed",
            context={"upload_filename": filename},
        )
        raise HTTPException(status_code=500, detail="Upload could not be stored") from exc
    finally:
        tmp_path.unlink(missing_ok=True)


def validate_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise HTTPException(status_code=400, detail="Relative path is invalid")
    return path.as_posix()
