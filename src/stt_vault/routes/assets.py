import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, UploadFile
from openai import OpenAI

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
            try:
                db.create_asset(
                    settings.stt_db_path, asset_id, file.filename, media_type, stored_path
                )
            except Exception:
                shutil.rmtree(settings.media_dir / asset_id, ignore_errors=True)
                raise
        finally:
            tmp_path.unlink(missing_ok=True)

        return {"id": asset_id, "status": "queued"}

    @router.post("/api/assets/batch", dependencies=[Depends(require_admin)])
    async def upload_assets_batch(
        files: Annotated[list[UploadFile], File()],
        relative_paths: Annotated[list[str], Form()],
    ) -> dict[str, list[dict[str, str]]]:
        if len(files) != len(relative_paths):
            raise HTTPException(status_code=400, detail="Each file requires one relative path")

        results = []
        for file, relative_path in zip(files, relative_paths, strict=True):
            try:
                filename = _validated_relative_path(relative_path)
                asset_id = await _store_uploaded_file(file, filename, settings)
            except HTTPException as exc:
                results.append(
                    {"path": relative_path, "status": "failed", "detail": str(exc.detail)}
                )
            except Exception as exc:
                results.append({"path": relative_path, "status": "failed", "detail": str(exc)})
            else:
                results.append({"path": relative_path, "status": "queued", "id": asset_id})
        return {"results": results}

    @router.get("/api/assets")
    def list_assets(_: Annotated[None, Depends(require_admin)]) -> list[dict]:
        return db.list_assets(settings.stt_db_path)

    @router.get("/api/jobs")
    def list_jobs(_: Annotated[None, Depends(require_admin)]) -> list[dict]:
        return db.list_jobs(settings.stt_db_path)

    app.include_router(router)


async def _store_uploaded_file(file: UploadFile, filename: str, settings: Settings) -> str:
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = Path(tmp.name)
        copied = 0
        max_bytes = settings.max_upload_mb * 1024 * 1024
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
    finally:
        tmp_path.unlink(missing_ok=True)


def _validated_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise HTTPException(status_code=400, detail="Relative path is invalid")
    return path.as_posix()


def register_asset_detail_routes(app: FastAPI, settings: Settings) -> None:
    router = APIRouter()

    @router.get("/api/assets/{asset_id}")
    def get_asset(asset_id: str, _: Annotated[None, Depends(require_admin)]) -> dict:
        asset = db.get_asset(settings.stt_db_path, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="Asset not found")
        return asset

    app.include_router(router)


def register_asset_summary_routes(app: FastAPI, settings: Settings) -> None:
    router = APIRouter()

    @router.post("/api/assets/{asset_id}/summary", dependencies=[Depends(require_admin)])
    def summarize_asset(asset_id: str) -> dict[str, str]:
        asset = db.get_asset(settings.stt_db_path, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="Asset not found")
        if asset["status"] != "success" or not asset.get("transcript_segments"):
            raise HTTPException(status_code=409, detail="A completed transcript is required")
        transcript = "\n".join(segment["text"] for segment in asset["transcript_segments"])
        db.update_asset_summary(settings.stt_db_path, asset_id, status="running")
        try:
            client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)
            response = client.chat.completions.create(
                model=settings.openai_summary_model,
                messages=[
                    {"role": "user", "content": f"Summarize this transcript:\n\n{transcript}"}
                ],
            )
            text = response.choices[0].message.content or ""
        except Exception as exc:
            db.update_asset_summary(
                settings.stt_db_path,
                asset_id,
                status="failed",
                error=str(exc),
                model=settings.openai_summary_model,
            )
            raise HTTPException(status_code=502, detail="Summary generation failed") from exc
        db.update_asset_summary(
            settings.stt_db_path,
            asset_id,
            status="success",
            text=text,
            model=settings.openai_summary_model,
        )
        return {"status": "success", "summary": text}

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
        media_path = settings.media_dir / asset_id
        exports_path = settings.exports_dir / asset_id
        db.delete_asset_with_cleanup_task(settings.stt_db_path, asset_id, media_path, exports_path)
        _retry_cleanup(settings.stt_db_path, asset_id)
        return {"status": "deleted"}

    app.include_router(router)


def register_asset_cleanup_routes(app: FastAPI, settings: Settings) -> None:
    router = APIRouter()

    @router.post("/api/assets/{asset_id}/cleanup", dependencies=[Depends(require_admin)])
    def retry_cleanup(asset_id: str) -> dict[str, str]:
        if not _retry_cleanup(settings.stt_db_path, asset_id):
            raise HTTPException(status_code=404, detail="Cleanup task not found")
        return {"status": "deleted"}

    app.include_router(router)


def _retry_cleanup(db_path: Path, asset_id: str) -> bool:
    task = db.get_cleanup_task(db_path, asset_id)
    if task is None:
        return False
    for path in (task["media_path"], task["exports_path"]):
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            continue
        except OSError:
            return False
    db.clear_cleanup_task(db_path, asset_id)
    return True
