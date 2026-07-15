from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse

from .. import db
from ..app_services import detect_asset_visual_events
from ..auth import require_admin, require_resource_access
from ..settings import Settings
from ..visual import extract_thumbnail, visual_event_thumbnail_path

__all__ = ["register_asset_visual_event_routes"]


def register_asset_visual_event_routes(app: FastAPI, settings: Settings) -> None:
    router = APIRouter()

    @router.get("/api/assets/{asset_id}/visual-events")
    def get_visual_events(asset_id: str, _: Annotated[None, Depends(require_admin)]) -> list[dict]:
        if db.get_asset(settings.stt_db_path, asset_id) is None:
            raise HTTPException(status_code=404, detail="Asset not found")
        return db.list_visual_events(settings.stt_db_path, asset_id)

    @router.post("/api/assets/{asset_id}/visual-events", dependencies=[Depends(require_admin)])
    def detect_visual_events(asset_id: str) -> dict[str, int]:
        asset = db.get_asset(settings.stt_db_path, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="Asset not found")
        events = detect_asset_visual_events(settings, asset)
        return {"events": len(events)}

    @router.get("/api/assets/{asset_id}/visual-events/{event_index}/thumbnail")
    def get_visual_event_thumbnail(
        asset_id: str,
        event_index: int,
        _: Annotated[None, Depends(require_resource_access)],
    ) -> FileResponse:
        asset = db.get_asset(settings.stt_db_path, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="Asset not found")
        events = db.list_visual_events(settings.stt_db_path, asset_id)
        if event_index < 0 or event_index >= len(events):
            raise HTTPException(status_code=404, detail="Visual event not found")

        path = visual_event_thumbnail_path(settings.exports_dir, asset_id, event_index)
        if not path.exists():
            timestamp = float(events[event_index]["timestamp"])
            extract_thumbnail(Path(asset["original_path"]), path, timestamp)
        return FileResponse(path)

    app.include_router(router)
