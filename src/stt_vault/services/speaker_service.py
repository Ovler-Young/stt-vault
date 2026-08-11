from uuid import uuid4

from fastapi import HTTPException

from stt_vault.core.config import Settings
from stt_vault.core.models.mod_contracts import EmbeddingSpaceV1
from stt_vault.core.models.persistence_errors import EmbeddingSpaceConflictError
from stt_vault.core.models.records import AssetRecord, SpeakerRelabel
from stt_vault.persistence.sqlite_database import SqliteDatabase
from stt_vault.processing.diarization import match_speakers


def require_cosine_embedding_space(value: object) -> EmbeddingSpaceV1:
    if not isinstance(value, EmbeddingSpaceV1) or value.metric != "cosine":
        raise EmbeddingSpaceConflictError("Speaker embedding space is incompatible")
    return value


def clean_display_name(display_name: str) -> str:
    value = display_name.strip()
    if not value:
        raise HTTPException(status_code=400, detail="Display name is required")
    if len(value) > 120:
        raise HTTPException(status_code=400, detail="Display name is too long")
    return value


def resolve_speaker_id(
    settings: Settings,
    database: SqliteDatabase,
    asset: AssetRecord,
    local_speaker: str,
    display_name: str,
) -> str:
    for segment in asset.transcript_segments:
        if segment.speaker != local_speaker:
            continue
        speaker_id = segment.speaker_id
        if speaker_id and speaker_id != local_speaker:
            return speaker_id

    existing = database.find_speaker_by_display_name(display_name)
    if existing is not None:
        return existing.id

    return f"spk_{uuid4().hex[:12]}"


def count_local_speaker_segments(asset: AssetRecord, local_speaker: str) -> int:
    return max(
        1,
        sum(1 for segment in asset.transcript_segments if segment.speaker == local_speaker),
    )


def recompute_asset_speaker_matches(
    settings: Settings, database: SqliteDatabase, asset_ids: list[str]
) -> list[str]:
    updated_asset_ids = []
    known_speakers = database.list_speakers()
    for asset_id in dict.fromkeys(asset_ids):
        asset = database.get_asset(asset_id)
        if asset is None:
            continue

        centroids = asset.speaker_centroids.as_dict()
        transcript_segments = asset.transcript_segments
        if not centroids or not transcript_segments:
            continue
        try:
            embedding_space = require_cosine_embedding_space(asset.embedding_space)
        except EmbeddingSpaceConflictError:
            continue

        matches = match_speakers(
            centroids,
            known_speakers,
            settings.speaker_similarity_threshold,
            embedding_space=embedding_space,
        )
        for local_speaker, match in matches.items():
            database.relabel_asset_speaker(
                SpeakerRelabel(
                    asset_id,
                    local_speaker,
                    match.speaker_id,
                    match.display_name,
                    match.score,
                )
            )
        updated_asset_ids.append(asset_id)
    return updated_asset_ids


__all__ = [
    "clean_display_name",
    "count_local_speaker_segments",
    "recompute_asset_speaker_matches",
    "require_cosine_embedding_space",
    "resolve_speaker_id",
]
