import shutil
import tempfile
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .media import store_upload
from .settings import Settings, get_settings
from .worker import Worker


def require_admin(
    settings: Annotated[Settings, Depends(get_settings)],
    x_stt_admin_password: Annotated[str | None, Header()] = None,
) -> None:
    if not settings.admin_password:
        return
    if x_stt_admin_password != settings.admin_password:
        raise HTTPException(status_code=401, detail="Missing or invalid admin password")


def create_app() -> FastAPI:
    settings = get_settings()
    settings.stt_data_dir.mkdir(parents=True, exist_ok=True)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    settings.exports_dir.mkdir(parents=True, exist_ok=True)
    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    db.initialize(settings.stt_db_path)

    app = FastAPI(title="STT Vault")
    worker = Worker(settings)

    @app.on_event("startup")
    def on_startup() -> None:
        worker.start()

    @app.on_event("shutdown")
    def on_shutdown() -> None:
        worker.stop()

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/config")
    def config() -> dict[str, object]:
        return {
            "auth_required": bool(settings.admin_password),
            "transcribe_model": settings.openai_transcribe_model,
            "senko_device": settings.senko_device,
            "batched_embeddings_requested": settings.senko_batched_embeddings,
        }

    @app.post("/api/assets", dependencies=[Depends(require_admin)])
    async def upload_asset(file: UploadFile = File()) -> dict[str, str]:
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

    @app.get("/api/assets")
    def list_assets(_: Annotated[None, Depends(require_admin)]) -> list[dict]:
        return db.list_assets(settings.stt_db_path)

    @app.get("/api/assets/{asset_id}")
    def get_asset(asset_id: str, _: Annotated[None, Depends(require_admin)]) -> dict:
        asset = db.get_asset(settings.stt_db_path, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="Asset not found")
        return asset

    @app.get("/api/assets/{asset_id}/media")
    def get_media(asset_id: str, _: Annotated[None, Depends(require_admin)]) -> FileResponse:
        asset = db.get_asset(settings.stt_db_path, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="Asset not found")
        return FileResponse(asset["original_path"], filename=asset["filename"])

    @app.get("/api/assets/{asset_id}/exports/{format_name}")
    def get_export(
        asset_id: str,
        format_name: str,
        _: Annotated[None, Depends(require_admin)],
    ) -> FileResponse:
        asset = db.get_asset(settings.stt_db_path, asset_id)
        if asset is None or not asset.get("exports") or format_name not in asset["exports"]:
            raise HTTPException(status_code=404, detail="Export not found")
        return FileResponse(asset["exports"][format_name])

    @app.delete("/api/assets/{asset_id}", dependencies=[Depends(require_admin)])
    def delete_asset(asset_id: str) -> dict[str, str]:
        asset = db.get_asset(settings.stt_db_path, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="Asset not found")
        with db.transaction(settings.stt_db_path) as conn:
            conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
        shutil.rmtree(settings.media_dir / asset_id, ignore_errors=True)
        shutil.rmtree(settings.exports_dir / asset_id, ignore_errors=True)
        return {"status": "deleted"}

    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app


def run() -> None:
    settings = get_settings()
    uvicorn.run("stt_vault.app:create_app", factory=True, host=settings.app_host, port=settings.app_port)


if __name__ == "__main__":
    run()

