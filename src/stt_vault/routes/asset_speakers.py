from fastapi import APIRouter, Depends, FastAPI, HTTPException

from .. import db
from ..app_services import (
    clean_display_name,
    count_local_speaker_segments,
    recompute_asset_speaker_matches,
    resolve_speaker_id,
    rewrite_asset_exports,
)
from ..auth import require_admin
from ..requests import SpeakerNameRequest
from ..settings import Settings

__all__ = ["register_asset_speaker_routes"]


def register_asset_speaker_routes(app: FastAPI, settings: Settings) -> None:
    router = APIRouter()

    @router.post(
        "/api/assets/{asset_id}/speakers/{local_speaker}",
        dependencies=[Depends(require_admin)],
    )
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

    @router.post(
        "/api/assets/{asset_id}/speaker-matches/recompute",
        dependencies=[Depends(require_admin)],
    )
    def recompute_asset_speakers(asset_id: str) -> dict[str, int]:
        if db.get_asset(settings.stt_db_path, asset_id) is None:
            raise HTTPException(status_code=404, detail="Asset not found")
        updated_asset_ids = recompute_asset_speaker_matches(settings, [asset_id])
        rewrite_asset_exports(settings, updated_asset_ids)
        return {"assets": len(updated_asset_ids)}

    app.include_router(router)
