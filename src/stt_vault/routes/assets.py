import shutil
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, File, HTTPException, UploadFile

from .. import db
from ..auth import require_admin
from ..media import store_upload
from ..settings import Settings

__all__ = [
    "register_asset_collection_routes",
    "register_asset_delete_route",
    "register_asset_detail_routes",
    "register_asset_event_routes",
    "register_asset_retry_route",
]


def register_asset_collection_routes(app: FastAPI, settings: Settings) -> None:
    router = APIRouter()

    @router.post("/api/assets", dependencies=[Depends(require_admin)])
    async def upload_asset(file: Annotated[UploadFile, File()]) -> dict[str, str]:
        if not file.filename:
            raise HTTPException(status_code=400, detail="Filename is required")

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = Path(tmp.name)
            copied = 0
            max_bytes = settings.max_upload_mb * 1024 * 1024
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                copied += len(chunk)
                if copied > max_bytes:
                    tmp_path.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail="Upload is too large")
                tmp.write(chunk)

        try:
            asset_id, stored_path, media_type = store_upload(
                settings.media_dir,
                file.filename,
                tmp_path,
            )
            db.create_asset(settings.stt_db_path, asset_id, file.filename, media_type, stored_path)
        finally:
            tmp_path.unlink(missing_ok=True)

        return {"id": asset_id, "status": "queued"}

    @router.get("/api/assets")
    def list_assets(_: Annotated[None, Depends(require_admin)]) -> list[dict]:
        return db.list_assets(settings.stt_db_path)

    @router.get("/api/jobs")
    def list_jobs(_: Annotated[None, Depends(require_admin)]) -> list[dict]:
        return db.list_jobs(settings.stt_db_path)

    app.include_router(router)


def register_asset_detail_routes(app: FastAPI, settings: Settings) -> None:
    router = APIRouter()

    @router.get("/api/assets/{asset_id}")
    def get_asset(asset_id: str, _: Annotated[None, Depends(require_admin)]) -> dict:
        asset = db.get_asset(settings.stt_db_path, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="Asset not found")
        return asset

    app.include_router(router)


def register_asset_event_routes(app: FastAPI, settings: Settings) -> None:
    router = APIRouter()

    @router.get("/api/assets/{asset_id}/events")
    def get_asset_events(asset_id: str, _: Annotated[None, Depends(require_admin)]) -> list[dict]:
        if db.get_asset(settings.stt_db_path, asset_id) is None:
            raise HTTPException(status_code=404, detail="Asset not found")
        return db.list_events(settings.stt_db_path, asset_id)

    app.include_router(router)


def register_asset_retry_route(app: FastAPI, settings: Settings) -> None:
    router = APIRouter()

    @router.post("/api/assets/{asset_id}/retry", dependencies=[Depends(require_admin)])
    def retry_asset(asset_id: str) -> dict[str, str]:
        try:
            db.retry_asset(settings.stt_db_path, asset_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Asset not found") from None
        return {"status": "queued"}

    app.include_router(router)


def register_asset_delete_route(app: FastAPI, settings: Settings) -> None:
    router = APIRouter()

    @router.delete("/api/assets/{asset_id}", dependencies=[Depends(require_admin)])
    def delete_asset(asset_id: str) -> dict[str, str]:
        asset = db.get_asset(settings.stt_db_path, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="Asset not found")
        with db.transaction(settings.stt_db_path) as conn:
            conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
        shutil.rmtree(settings.media_dir / asset_id, ignore_errors=True)
        shutil.rmtree(settings.exports_dir / asset_id, ignore_errors=True)
        return {"status": "deleted"}

    app.include_router(router)
