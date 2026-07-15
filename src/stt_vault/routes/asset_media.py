from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from .. import db
from ..app_services import stream_process_stdout
from ..auth import require_admin, require_resource_access
from ..media import ffprobe_audio_streams, playback_media_stream_command
from ..settings import Settings

__all__ = ["register_asset_media_routes"]


def register_asset_media_routes(app: FastAPI, settings: Settings) -> None:
    router = APIRouter()

    @router.get("/api/assets/{asset_id}/audio-tracks")
    def get_audio_tracks(asset_id: str, _: Annotated[None, Depends(require_admin)]) -> list[dict]:
        asset = db.get_asset(settings.stt_db_path, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="Asset not found")
        try:
            return ffprobe_audio_streams(Path(asset["original_path"]))
        except Exception as exc:
            detail = f"Could not probe audio tracks: {exc}"
            raise HTTPException(status_code=400, detail=detail) from exc

    @router.get("/api/assets/{asset_id}/media", response_model=None)
    def get_media(
        asset_id: str,
        _: Annotated[None, Depends(require_resource_access)],
        audio_track: str | None = None,
    ) -> FileResponse | StreamingResponse:
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

    @router.get("/api/assets/{asset_id}/exports/{format_name}")
    def get_export(
        asset_id: str,
        format_name: str,
        _: Annotated[None, Depends(require_resource_access)],
    ) -> FileResponse:
        asset = db.get_asset(settings.stt_db_path, asset_id)
        if asset is None or not asset.get("exports") or format_name not in asset["exports"]:
            raise HTTPException(status_code=404, detail="Export not found")
        return FileResponse(asset["exports"][format_name])

    app.include_router(router)
