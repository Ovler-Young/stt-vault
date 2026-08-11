from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException

from stt_vault.core.auth import require_admin
from stt_vault.core.config import Settings
from stt_vault.core.models.api import (
    SpeakerDeleteResponse,
    SpeakerRecomputeResponse,
    SpeakerResponse,
)
from stt_vault.core.models.persistence_errors import EmbeddingSpaceConflictError
from stt_vault.core.models.requests import SpeakerMergeRequest, SpeakerNameRequest
from stt_vault.persistence.sqlite_database import SqliteDatabase
from stt_vault.processing.asset_exports import rewrite_asset_exports
from stt_vault.services.speaker_service import (
    clean_display_name,
    recompute_asset_speaker_matches,
    require_cosine_embedding_space,
)

from .lookup import get_speaker_or_404, speaker_response

__all__ = ["register_speaker_routes"]


def register_speaker_routes(app: FastAPI, settings: Settings, database: SqliteDatabase) -> None:
    router = APIRouter()

    @router.get("/api/speakers", response_model=list[SpeakerResponse])
    def list_speakers(_: Annotated[None, Depends(require_admin)]) -> list[SpeakerResponse]:
        return [speaker_response(speaker) for speaker in database.list_speakers()]

    @router.put(
        "/api/speakers/{speaker_id}",
        dependencies=[Depends(require_admin)],
        response_model=SpeakerResponse,
    )
    def rename_speaker(speaker_id: str, payload: SpeakerNameRequest) -> SpeakerResponse:
        display_name = clean_display_name(payload.display_name)
        get_speaker_or_404(database, speaker_id)

        affected_asset_ids = database.list_asset_ids_for_speaker(speaker_id)
        database.rename_speaker(speaker_id, display_name)
        rewrite_asset_exports(settings, database, affected_asset_ids)
        return get_speaker_or_404(database, speaker_id)

    @router.delete(
        "/api/speakers/{speaker_id}",
        dependencies=[Depends(require_admin)],
        response_model=SpeakerDeleteResponse,
    )
    def delete_speaker(speaker_id: str) -> SpeakerDeleteResponse:
        get_speaker_or_404(database, speaker_id)

        affected_asset_ids = database.list_asset_ids_for_speaker(speaker_id)
        database.delete_speaker(speaker_id)
        rewrite_asset_exports(settings, database, affected_asset_ids)
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
        target = database.get_speaker(target_speaker_id)
        if target is None:
            raise HTTPException(status_code=404, detail="Target speaker not found")
        source = database.get_speaker(source_speaker_id)
        if source is None:
            raise HTTPException(status_code=404, detail="Source speaker not found")
        try:
            if require_cosine_embedding_space(
                source.embedding_space
            ) != require_cosine_embedding_space(target.embedding_space):
                raise EmbeddingSpaceConflictError("Speaker embedding space is incompatible")
        except EmbeddingSpaceConflictError as error:
            raise HTTPException(
                status_code=409, detail="Speaker embedding space is incompatible"
            ) from error

        affected_asset_ids = sorted(
            set(database.list_asset_ids_for_speaker(source_speaker_id))
            | set(database.list_asset_ids_for_speaker(target_speaker_id))
        )
        try:
            database.merge_speakers(source_speaker_id, target_speaker_id)
        except EmbeddingSpaceConflictError as error:
            raise HTTPException(
                status_code=409, detail="Speaker embedding space is incompatible"
            ) from error
        affected_asset_ids = recompute_asset_speaker_matches(settings, database, affected_asset_ids)
        rewrite_asset_exports(settings, database, affected_asset_ids)
        return get_speaker_or_404(database, target_speaker_id)

    @router.post(
        "/api/speakers/recompute",
        dependencies=[Depends(require_admin)],
        response_model=SpeakerRecomputeResponse,
    )
    def recompute_all_speakers() -> SpeakerRecomputeResponse:
        asset_ids = database.list_asset_ids_with_speaker_centroids()
        updated_asset_ids = recompute_asset_speaker_matches(settings, database, asset_ids)
        rewrite_asset_exports(settings, database, updated_asset_ids)
        return SpeakerRecomputeResponse(assets=len(updated_asset_ids))

    app.include_router(router)
