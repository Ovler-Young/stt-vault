import shutil
import tempfile
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import uvicorn
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db
from .exports import write_exports
from .media import store_upload
from .settings import Settings, get_settings
from .worker import Worker


class SpeakerNameRequest(BaseModel):
    display_name: str


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

    @app.get("/api/jobs")
    def list_jobs(_: Annotated[None, Depends(require_admin)]) -> list[dict]:
        return db.list_jobs(settings.stt_db_path)

    @app.get("/api/speakers")
    def list_speakers(_: Annotated[None, Depends(require_admin)]) -> list[dict]:
        return db.list_speakers(settings.stt_db_path)

    @app.put("/api/speakers/{speaker_id}", dependencies=[Depends(require_admin)])
    def rename_speaker(speaker_id: str, payload: SpeakerNameRequest) -> dict:
        display_name = clean_display_name(payload.display_name)
        if db.get_speaker(settings.stt_db_path, speaker_id) is None:
            raise HTTPException(status_code=404, detail="Speaker not found")

        affected_asset_ids = db.list_asset_ids_for_speaker(settings.stt_db_path, speaker_id)
        db.rename_speaker(settings.stt_db_path, speaker_id, display_name)
        rewrite_asset_exports(settings, affected_asset_ids)
        return db.get_speaker(settings.stt_db_path, speaker_id) or {}

    @app.delete("/api/speakers/{speaker_id}", dependencies=[Depends(require_admin)])
    def delete_speaker(speaker_id: str) -> dict[str, str]:
        if db.get_speaker(settings.stt_db_path, speaker_id) is None:
            raise HTTPException(status_code=404, detail="Speaker not found")

        affected_asset_ids = db.list_asset_ids_for_speaker(settings.stt_db_path, speaker_id)
        db.delete_speaker(settings.stt_db_path, speaker_id)
        rewrite_asset_exports(settings, affected_asset_ids)
        return {"status": "deleted"}

    @app.get("/api/assets/{asset_id}")
    def get_asset(asset_id: str, _: Annotated[None, Depends(require_admin)]) -> dict:
        asset = db.get_asset(settings.stt_db_path, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="Asset not found")
        return asset

    @app.post("/api/assets/{asset_id}/speakers/{local_speaker}", dependencies=[Depends(require_admin)])
    def save_asset_speaker(
        asset_id: str,
        local_speaker: str,
        payload: SpeakerNameRequest,
    ) -> dict:
        display_name = clean_display_name(payload.display_name)
        asset = db.get_asset(settings.stt_db_path, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="Asset not found")

        centroids = asset.get("speaker_centroids") or {}
        centroid = centroids.get(local_speaker)
        if centroid is None:
            raise HTTPException(status_code=400, detail="Speaker centroid is not available yet")

        speaker_id = resolve_speaker_id(settings, asset, local_speaker, display_name)
        sample_count = count_local_speaker_segments(asset, local_speaker)
        db.upsert_speaker(
            settings.stt_db_path,
            speaker_id,
            display_name,
            centroid,
            sample_count,
        )
        db.relabel_asset_speaker(
            settings.stt_db_path,
            asset_id,
            local_speaker,
            speaker_id,
            display_name,
            1.0,
        )
        rewrite_asset_exports(settings, [asset_id])
        return db.get_speaker(settings.stt_db_path, speaker_id) or {}

    @app.get("/api/assets/{asset_id}/events")
    def get_asset_events(asset_id: str, _: Annotated[None, Depends(require_admin)]) -> list[dict]:
        if db.get_asset(settings.stt_db_path, asset_id) is None:
            raise HTTPException(status_code=404, detail="Asset not found")
        return db.list_events(settings.stt_db_path, asset_id)

    @app.post("/api/assets/{asset_id}/retry", dependencies=[Depends(require_admin)])
    def retry_asset(asset_id: str) -> dict[str, str]:
        try:
            db.retry_asset(settings.stt_db_path, asset_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Asset not found") from None
        return {"status": "queued"}

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
        static_root = static_dir.resolve()
        app.mount("/_app", StaticFiles(directory=static_root / "_app"), name="static")

        @app.get("/{full_path:path}", include_in_schema=False)
        def serve_spa(full_path: str) -> FileResponse:
            if full_path == "api" or full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="Not found")

            candidate = (static_root / full_path).resolve()
            if candidate.is_file() and candidate.is_relative_to(static_root):
                return FileResponse(candidate)

            index_path = static_root / "index.html"
            if not index_path.exists():
                raise HTTPException(status_code=404, detail="Frontend not built")
            return FileResponse(index_path)

    return app


def clean_display_name(display_name: str) -> str:
    value = display_name.strip()
    if not value:
        raise HTTPException(status_code=400, detail="Display name is required")
    if len(value) > 120:
        raise HTTPException(status_code=400, detail="Display name is too long")
    return value


def resolve_speaker_id(
    settings: Settings,
    asset: dict,
    local_speaker: str,
    display_name: str,
) -> str:
    for segment in asset.get("transcript_segments") or []:
        if segment.get("speaker") != local_speaker:
            continue
        speaker_id = segment.get("speaker_id")
        if speaker_id and speaker_id != local_speaker:
            return speaker_id

    existing = db.find_speaker_by_display_name(settings.stt_db_path, display_name)
    if existing is not None:
        return existing["id"]

    return f"spk_{uuid4().hex[:12]}"


def count_local_speaker_segments(asset: dict, local_speaker: str) -> int:
    return max(
        1,
        sum(1 for segment in asset.get("transcript_segments") or [] if segment["speaker"] == local_speaker),
    )


def rewrite_asset_exports(settings: Settings, asset_ids: list[str]) -> None:
    for asset_id in asset_ids:
        asset = db.get_asset(settings.stt_db_path, asset_id)
        if asset is None:
            continue

        transcript_segments = asset.get("transcript_segments") or []
        raw_segments = asset.get("raw_segments") or []
        if not transcript_segments or not raw_segments:
            continue

        exports = write_exports(
            settings.exports_dir,
            asset_id,
            asset["filename"],
            transcript_segments,
            raw_segments,
            settings.parsed_export_formats,
        )
        db.update_asset_exports(settings.stt_db_path, asset_id, exports)


def run() -> None:
    settings = get_settings()
    uvicorn.run("stt_vault.app:create_app", factory=True, host=settings.app_host, port=settings.app_port)


if __name__ == "__main__":
    run()
