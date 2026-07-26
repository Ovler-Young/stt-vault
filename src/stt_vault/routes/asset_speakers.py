from fastapi import APIRouter, Depends, FastAPI, HTTPException

from stt_vault.core.auth import require_admin
from stt_vault.core.requests import SpeakerNameRequest
from stt_vault.core.settings import Settings
from stt_vault.persistence import db
from stt_vault.processing.asset_exports import rewrite_asset_exports
from stt_vault.services.speaker_service import (
    clean_display_name,
    count_local_speaker_segments,
    recompute_asset_speaker_matches,
    resolve_speaker_id,
)

from .asset_lookup import get_asset_or_404

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
        asset = get_asset_or_404(settings.stt_db_path, asset_id)

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
        get_asset_or_404(settings.stt_db_path, asset_id)
        updated_asset_ids = recompute_asset_speaker_matches(settings, [asset_id])
        rewrite_asset_exports(settings, updated_asset_ids)
        return {"assets": len(updated_asset_ids)}

    app.include_router(router)
