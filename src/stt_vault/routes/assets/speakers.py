from fastapi import APIRouter, Depends, FastAPI, HTTPException

from stt_vault.core.auth import require_admin
from stt_vault.core.config import Settings
from stt_vault.core.models.api import SpeakerRecomputeResponse, SpeakerResponse
from stt_vault.core.models.persistence_errors import EmbeddingSpaceConflictError
from stt_vault.core.models.records import SpeakerRelabel, SpeakerUpsert
from stt_vault.core.models.requests import SpeakerNameRequest
from stt_vault.persistence.sqlite_database import SqliteDatabase
from stt_vault.processing.asset_exports import rewrite_asset_exports
from stt_vault.services.speaker_service import (
    clean_display_name,
    count_local_speaker_segments,
    recompute_asset_speaker_matches,
    require_cosine_embedding_space,
    resolve_speaker_id,
)

from ..speakers.lookup import get_speaker_or_404
from .lookup import get_asset_or_404

__all__ = ["register_asset_speaker_routes"]


def register_asset_speaker_routes(
    app: FastAPI, settings: Settings, database: SqliteDatabase
) -> None:
    router = APIRouter()

    @router.post(
        "/api/assets/{asset_id}/speakers/{local_speaker}",
        dependencies=[Depends(require_admin)],
        response_model=SpeakerResponse,
    )
    def save_asset_speaker(
        asset_id: str,
        local_speaker: str,
        payload: SpeakerNameRequest,
    ) -> SpeakerResponse:
        display_name = clean_display_name(payload.display_name)
        asset = get_asset_or_404(database, asset_id)

        centroids = asset.speaker_centroids.as_dict()
        centroid = centroids.get(local_speaker)
        if centroid is None:
            raise HTTPException(status_code=400, detail="Speaker centroid is not available yet")
        try:
            embedding_space = require_cosine_embedding_space(asset.embedding_space)
        except EmbeddingSpaceConflictError as error:
            raise HTTPException(
                status_code=409, detail="Speaker embedding space is incompatible"
            ) from error

        speaker_id = resolve_speaker_id(settings, database, asset, local_speaker, display_name)
        existing = database.get_speaker(speaker_id)
        if existing is not None:
            try:
                if require_cosine_embedding_space(existing.embedding_space) != embedding_space:
                    raise EmbeddingSpaceConflictError("Speaker embedding space is incompatible")
            except EmbeddingSpaceConflictError as error:
                raise HTTPException(
                    status_code=409, detail="Speaker embedding space is incompatible"
                ) from error
        sample_count = count_local_speaker_segments(asset, local_speaker)
        try:
            database.upsert_speaker(
                SpeakerUpsert(speaker_id, display_name, centroid, sample_count, embedding_space)
            )
        except EmbeddingSpaceConflictError as error:
            raise HTTPException(
                status_code=409, detail="Speaker embedding space is incompatible"
            ) from error
        database.relabel_asset_speaker(
            SpeakerRelabel(asset_id, local_speaker, speaker_id, display_name, 1.0)
        )
        updated_asset_ids = recompute_asset_speaker_matches(
            settings, database, database.list_asset_ids_with_speaker_centroids()
        )
        rewrite_asset_exports(settings, database, updated_asset_ids)
        return get_speaker_or_404(database, speaker_id)

    @router.post(
        "/api/assets/{asset_id}/speaker-matches/recompute",
        dependencies=[Depends(require_admin)],
        response_model=SpeakerRecomputeResponse,
    )
    def recompute_asset_speakers(asset_id: str) -> SpeakerRecomputeResponse:
        get_asset_or_404(database, asset_id)
        updated_asset_ids = recompute_asset_speaker_matches(settings, database, [asset_id])
        rewrite_asset_exports(settings, database, updated_asset_ids)
        return SpeakerRecomputeResponse(assets=len(updated_asset_ids))

    app.include_router(router)
