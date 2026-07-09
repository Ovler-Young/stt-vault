from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException

from .. import db
from ..app_services import (
    clean_display_name,
    recompute_asset_speaker_matches,
    rewrite_asset_exports,
)
from ..auth import require_admin
from ..requests import SpeakerMergeRequest, SpeakerNameRequest
from ..settings import Settings

__all__ = ["register_speaker_routes"]


def register_speaker_routes(app: FastAPI, settings: Settings) -> None:
    router = APIRouter()

    @router.get("/api/speakers")
    def list_speakers(_: Annotated[None, Depends(require_admin)]) -> list[dict]:
        return db.list_speakers(settings.stt_db_path)

    @router.put("/api/speakers/{speaker_id}", dependencies=[Depends(require_admin)])
    def rename_speaker(speaker_id: str, payload: SpeakerNameRequest) -> dict:
        display_name = clean_display_name(payload.display_name)
        if db.get_speaker(settings.stt_db_path, speaker_id) is None:
            raise HTTPException(status_code=404, detail="Speaker not found")

        affected_asset_ids = db.list_asset_ids_for_speaker(settings.stt_db_path, speaker_id)
        db.rename_speaker(settings.stt_db_path, speaker_id, display_name)
        rewrite_asset_exports(settings, affected_asset_ids)
        return db.get_speaker(settings.stt_db_path, speaker_id) or {}

    @router.delete("/api/speakers/{speaker_id}", dependencies=[Depends(require_admin)])
    def delete_speaker(speaker_id: str) -> dict[str, str]:
        if db.get_speaker(settings.stt_db_path, speaker_id) is None:
            raise HTTPException(status_code=404, detail="Speaker not found")

        affected_asset_ids = db.list_asset_ids_for_speaker(settings.stt_db_path, speaker_id)
        db.delete_speaker(settings.stt_db_path, speaker_id)
        rewrite_asset_exports(settings, affected_asset_ids)
        return {"status": "deleted"}

    @router.post("/api/speakers/{target_speaker_id}/merge", dependencies=[Depends(require_admin)])
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

    @router.post("/api/speakers/recompute", dependencies=[Depends(require_admin)])
    def recompute_all_speakers() -> dict[str, int]:
        asset_ids = db.list_asset_ids_with_speaker_centroids(settings.stt_db_path)
        updated_asset_ids = recompute_asset_speaker_matches(settings, asset_ids)
        rewrite_asset_exports(settings, updated_asset_ids)
        return {"assets": len(updated_asset_ids)}

    app.include_router(router)
