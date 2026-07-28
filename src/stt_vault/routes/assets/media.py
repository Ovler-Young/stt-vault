import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from stt_vault.core.auth import require_admin, require_resource_access
from stt_vault.core.config import Settings
from stt_vault.core.diagnostics.logging import log_exception_diagnostic
from stt_vault.core.models.records import AudioStream
from stt_vault.processing.media import ffprobe_audio_streams, playback_media_stream_command
from stt_vault.services.media_streaming import stream_process_stdout

from .lookup import get_asset_or_404

__all__ = ["register_asset_media_routes"]
logger = logging.getLogger(__name__)


def register_asset_media_routes(app: FastAPI, settings: Settings) -> None:
    router = APIRouter()

    @router.get("/api/assets/{asset_id}/audio-tracks")
    def get_audio_tracks(
        asset_id: str, _: Annotated[None, Depends(require_admin)]
    ) -> list[AudioStream]:
        asset = get_asset_or_404(settings.stt_db_path, asset_id, include_event_history=False)
        try:
            return ffprobe_audio_streams(Path(asset["original_path"]))
        except Exception as exc:
            log_exception_diagnostic(
                logger,
                "audio track probe failed",
                exc,
                event_name="media.audio_track_probe_failed",
                context={"asset_id": asset_id},
            )
            raise HTTPException(status_code=400, detail="Could not probe audio tracks") from exc

    @router.get("/api/assets/{asset_id}/media", response_model=None)
    def get_media(
        asset_id: str,
        _: Annotated[None, Depends(require_resource_access)],
        audio_track: str | None = None,
    ) -> FileResponse | StreamingResponse:
        asset = get_asset_or_404(settings.stt_db_path, asset_id, include_event_history=False)
        if not audio_track or audio_track == "default":
            return FileResponse(asset["original_path"], filename=asset["filename"])
        try:
            command = playback_media_stream_command(Path(asset["original_path"]), audio_track)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return StreamingResponse(
            stream_process_stdout(command, asset_id=asset_id),
            media_type="video/mp4",
            headers={"Accept-Ranges": "none"},
        )

    @router.get("/api/assets/{asset_id}/exports/{format_name}")
    def get_export(
        asset_id: str,
        format_name: str,
        _: Annotated[None, Depends(require_resource_access)],
    ) -> FileResponse:
        asset = get_asset_or_404(settings.stt_db_path, asset_id, include_event_history=False)
        if not asset.get("exports") or format_name not in asset["exports"]:
            raise HTTPException(status_code=404, detail="Export not found")
        return FileResponse(asset["exports"][format_name])

    app.include_router(router)
