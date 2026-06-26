import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import uvicorn
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db
from .diarization import match_speakers
from .exports import write_exports
from .media import ffprobe_audio_streams, playback_media_stream_command, store_upload
from .settings import Settings, get_settings
from .visual import (
    detect_slide_changes,
    extract_thumbnail,
    visual_event_thumbnail_path,
    write_visual_event_thumbnails,
    write_visual_events_export,
)
from .worker import Worker


class SpeakerNameRequest(BaseModel):
    display_name: str


class SpeakerMergeRequest(BaseModel):
    source_speaker_id: str


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

    @app.post("/api/speakers/{target_speaker_id}/merge", dependencies=[Depends(require_admin)])
    def merge_speaker(target_speaker_id: str, payload: SpeakerMergeRequest) -> dict:
        source_speaker_id = payload.source_speaker_id
        if source_speaker_id == target_speaker_id:
            raise HTTPException(status_code=400, detail="Choose two different speakers")
        if db.get_speaker(settings.stt_db_path, target_speaker_id) is None:
            raise HTTPException(status_code=404, detail="Target speaker not found")
        if db.get_speaker(settings.stt_db_path, source_speaker_id) is None:
            raise HTTPException(status_code=404, detail="Source speaker not found")

        affected_asset_ids = sorted(
            set(db.list_asset_ids_for_speaker(settings.stt_db_path, source_speaker_id))
            | set(db.list_asset_ids_for_speaker(settings.stt_db_path, target_speaker_id))
        )
        db.merge_speakers(settings.stt_db_path, source_speaker_id, target_speaker_id)
        affected_asset_ids = recompute_asset_speaker_matches(settings, affected_asset_ids)
        rewrite_asset_exports(settings, affected_asset_ids)
        return db.get_speaker(settings.stt_db_path, target_speaker_id) or {}

    @app.post("/api/speakers/recompute", dependencies=[Depends(require_admin)])
    def recompute_all_speakers() -> dict[str, int]:
        asset_ids = db.list_asset_ids_with_speaker_centroids(settings.stt_db_path)
        updated_asset_ids = recompute_asset_speaker_matches(settings, asset_ids)
        rewrite_asset_exports(settings, updated_asset_ids)
        return {"assets": len(updated_asset_ids)}

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
        updated_asset_ids = recompute_asset_speaker_matches(
            settings,
            db.list_asset_ids_with_speaker_centroids(settings.stt_db_path),
        )
        rewrite_asset_exports(settings, updated_asset_ids)
        return db.get_speaker(settings.stt_db_path, speaker_id) or {}

    @app.post("/api/assets/{asset_id}/speaker-matches/recompute", dependencies=[Depends(require_admin)])
    def recompute_asset_speakers(asset_id: str) -> dict[str, int]:
        if db.get_asset(settings.stt_db_path, asset_id) is None:
            raise HTTPException(status_code=404, detail="Asset not found")
        updated_asset_ids = recompute_asset_speaker_matches(settings, [asset_id])
        rewrite_asset_exports(settings, updated_asset_ids)
        return {"assets": len(updated_asset_ids)}

    @app.get("/api/assets/{asset_id}/events")
    def get_asset_events(asset_id: str, _: Annotated[None, Depends(require_admin)]) -> list[dict]:
        if db.get_asset(settings.stt_db_path, asset_id) is None:
            raise HTTPException(status_code=404, detail="Asset not found")
        return db.list_events(settings.stt_db_path, asset_id)

    @app.get("/api/assets/{asset_id}/visual-events")
    def get_visual_events(asset_id: str, _: Annotated[None, Depends(require_admin)]) -> list[dict]:
        if db.get_asset(settings.stt_db_path, asset_id) is None:
            raise HTTPException(status_code=404, detail="Asset not found")
        return db.list_visual_events(settings.stt_db_path, asset_id)

    @app.post("/api/assets/{asset_id}/visual-events", dependencies=[Depends(require_admin)])
    def detect_visual_events(asset_id: str) -> dict[str, int]:
        asset = db.get_asset(settings.stt_db_path, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="Asset not found")
        events = detect_asset_visual_events(settings, asset)
        return {"events": len(events)}

    @app.get("/api/assets/{asset_id}/visual-events/{event_index}/thumbnail")
    def get_visual_event_thumbnail(
        asset_id: str,
        event_index: int,
        _: Annotated[None, Depends(require_admin)],
    ) -> FileResponse:
        asset = db.get_asset(settings.stt_db_path, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="Asset not found")
        events = db.list_visual_events(settings.stt_db_path, asset_id)
        if event_index < 0 or event_index >= len(events):
            raise HTTPException(status_code=404, detail="Visual event not found")

        path = visual_event_thumbnail_path(settings.exports_dir, asset_id, event_index)
        if not path.exists():
            extract_thumbnail(Path(asset["original_path"]), path, float(events[event_index]["timestamp"]))
        return FileResponse(path)

    @app.post("/api/assets/{asset_id}/retry", dependencies=[Depends(require_admin)])
    def retry_asset(asset_id: str) -> dict[str, str]:
        try:
            db.retry_asset(settings.stt_db_path, asset_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Asset not found") from None
        return {"status": "queued"}

    @app.get("/api/assets/{asset_id}/audio-tracks")
    def get_audio_tracks(asset_id: str, _: Annotated[None, Depends(require_admin)]) -> list[dict]:
        asset = db.get_asset(settings.stt_db_path, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="Asset not found")
        try:
            return ffprobe_audio_streams(Path(asset["original_path"]))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Could not probe audio tracks: {exc}") from exc

    @app.get("/api/assets/{asset_id}/media", response_model=None)
    def get_media(
        asset_id: str,
        _: Annotated[None, Depends(require_admin)],
        audio_track: str | None = None,
    ):
        asset = db.get_asset(settings.stt_db_path, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="Asset not found")
        if not audio_track or audio_track == "default":
            return FileResponse(asset["original_path"], filename=asset["filename"])
        try:
            command = playback_media_stream_command(Path(asset["original_path"]), audio_track)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return StreamingResponse(
            stream_process_stdout(command),
            media_type="video/mp4",
            headers={"Accept-Ranges": "none"},
        )

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

            index_path = static_root / "200.html"
            if not index_path.exists():
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


def detect_asset_visual_events(settings: Settings, asset: dict) -> list[dict]:
    if asset.get("media_type") != "video":
        return []

    events = detect_slide_changes(
        Path(asset["original_path"]),
        sample_interval_seconds=settings.visual_sample_interval_seconds,
        threshold=settings.visual_change_threshold,
        min_gap_seconds=settings.visual_min_gap_seconds,
    )
    db.replace_visual_events(settings.stt_db_path, asset["id"], events)
    write_visual_event_thumbnails(
        Path(asset["original_path"]),
        settings.exports_dir,
        asset["id"],
        events,
    )
    exports = dict(asset.get("exports") or {})
    exports["visual_events"] = write_visual_events_export(
        settings.exports_dir,
        asset["id"],
        events,
    )
    db.update_asset_exports(settings.stt_db_path, asset["id"], exports)
    return events


def recompute_asset_speaker_matches(settings: Settings, asset_ids: list[str]) -> list[str]:
    updated_asset_ids = []
    known_speakers = db.list_speakers(settings.stt_db_path)
    for asset_id in dict.fromkeys(asset_ids):
        asset = db.get_asset(settings.stt_db_path, asset_id)
        if asset is None:
            continue

        centroids = asset.get("speaker_centroids") or {}
        transcript_segments = asset.get("transcript_segments") or []
        if not centroids or not transcript_segments:
            continue

        matches = match_speakers(
            centroids,
            known_speakers,
            settings.speaker_similarity_threshold,
        )
        db.relabel_asset_speakers(settings.stt_db_path, asset_id, matches)
        updated_asset_ids.append(asset_id)
    return updated_asset_ids


def stream_process_stdout(command: list[str]):
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        if process.stdout is None:
            return
        while True:
            chunk = process.stdout.read(1024 * 1024)
            if not chunk:
                break
            yield chunk
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        if process.stdout is not None:
            process.stdout.close()


def run() -> None:
    settings = get_settings()
    uvicorn.run("stt_vault.app:create_app", factory=True, host=settings.app_host, port=settings.app_port)


if __name__ == "__main__":
    run()
