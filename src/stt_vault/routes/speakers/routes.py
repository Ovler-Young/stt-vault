from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException

from stt_vault.core.api_models import (
    SpeakerDeleteResponse,
    SpeakerRecomputeResponse,
    SpeakerResponse,
)
from stt_vault.core.auth import require_admin
from stt_vault.core.requests import SpeakerMergeRequest, SpeakerNameRequest
from stt_vault.core.settings import Settings
from stt_vault.persistence import db
from stt_vault.processing.asset_exports import rewrite_asset_exports
from stt_vault.services.speaker_service import (
    clean_display_name,
    recompute_asset_speaker_matches,
)

from .lookup import get_speaker_or_404, speaker_response

__all__ = ["register_speaker_routes"]


def register_speaker_routes(app: FastAPI, settings: Settings) -> None:
    router = APIRouter()

    @router.get("/api/speakers", response_model=list[SpeakerResponse])
    def list_speakers(_: Annotated[None, Depends(require_admin)]) -> list[SpeakerResponse]:
        return [speaker_response(speaker) for speaker in db.list_speakers(settings.stt_db_path)]

    @router.put(
        "/api/speakers/{speaker_id}",
        dependencies=[Depends(require_admin)],
        response_model=SpeakerResponse,
    )
    def rename_speaker(speaker_id: str, payload: SpeakerNameRequest) -> SpeakerResponse:
        display_name = clean_display_name(payload.display_name)
        get_speaker_or_404(settings.stt_db_path, speaker_id)

        affected_asset_ids = db.list_asset_ids_for_speaker(settings.stt_db_path, speaker_id)
        db.rename_speaker(settings.stt_db_path, speaker_id, display_name)
        rewrite_asset_exports(settings, affected_asset_ids)
        return get_speaker_or_404(settings.stt_db_path, speaker_id)

    @router.delete(
        "/api/speakers/{speaker_id}",
        dependencies=[Depends(require_admin)],
        response_model=SpeakerDeleteResponse,
    )
    def delete_speaker(speaker_id: str) -> SpeakerDeleteResponse:
        get_speaker_or_404(settings.stt_db_path, speaker_id)

        affected_asset_ids = db.list_asset_ids_for_speaker(settings.stt_db_path, speaker_id)
        db.delete_speaker(settings.stt_db_path, speaker_id)
        rewrite_asset_exports(settings, affected_asset_ids)
        return SpeakerDeleteResponse(status="deleted")

    @router.post(
        "/api/speakers/{target_speaker_id}/merge",
        dependencies=[Depends(require_admin)],
        response_model=SpeakerResponse,
    )
    def merge_speaker(target_speaker_id: str, payload: SpeakerMergeRequest) -> SpeakerResponse:
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
        return get_speaker_or_404(settings.stt_db_path, target_speaker_id)

    @router.post(
        "/api/speakers/recompute",
        dependencies=[Depends(require_admin)],
        response_model=SpeakerRecomputeResponse,
    )
    def recompute_all_speakers() -> SpeakerRecomputeResponse:
        asset_ids = db.list_asset_ids_with_speaker_centroids(settings.stt_db_path)
        updated_asset_ids = recompute_asset_speaker_matches(settings, asset_ids)
        rewrite_asset_exports(settings, updated_asset_ids)
        return SpeakerRecomputeResponse(assets=len(updated_asset_ids))

    app.include_router(router)
