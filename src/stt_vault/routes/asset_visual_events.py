from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse

from stt_vault.core.auth import require_admin, require_resource_access
from stt_vault.core.settings import Settings
from stt_vault.core.types import VisualEvent
from stt_vault.persistence import db
from stt_vault.processing.asset_visual_events import detect_asset_visual_events
from stt_vault.processing.visual import extract_thumbnail, visual_event_thumbnail_path

from .asset_lookup import get_asset_or_404

__all__ = ["register_asset_visual_event_routes"]


def register_asset_visual_event_routes(app: FastAPI, settings: Settings) -> None:
    router = APIRouter()

    @router.get("/api/assets/{asset_id}/visual-events")
    def get_visual_events(
        asset_id: str, _: Annotated[None, Depends(require_admin)]
    ) -> list[VisualEvent]:
        get_asset_or_404(settings.stt_db_path, asset_id)
        return db.list_visual_events(settings.stt_db_path, asset_id)

    @router.post("/api/assets/{asset_id}/visual-events", dependencies=[Depends(require_admin)])
    def detect_visual_events(asset_id: str) -> dict[str, int]:
        asset = get_asset_or_404(settings.stt_db_path, asset_id)
        events = detect_asset_visual_events(settings, asset)
        return {"events": len(events)}

    @router.get("/api/assets/{asset_id}/visual-events/{event_index}/thumbnail")
    def get_visual_event_thumbnail(
        asset_id: str,
        event_index: int,
        _: Annotated[None, Depends(require_resource_access)],
    ) -> FileResponse:
        asset = get_asset_or_404(settings.stt_db_path, asset_id)
        events = db.list_visual_events(settings.stt_db_path, asset_id)
        if event_index < 0 or event_index >= len(events):
            raise HTTPException(status_code=404, detail="Visual event not found")

        path = visual_event_thumbnail_path(settings.exports_dir, asset_id, event_index)
        if not path.exists():
            timestamp = float(events[event_index]["timestamp"])
            extract_thumbnail(Path(asset["original_path"]), path, timestamp)
        return FileResponse(path)

    app.include_router(router)
