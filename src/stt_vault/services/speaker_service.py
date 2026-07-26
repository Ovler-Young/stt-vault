from uuid import uuid4

from fastapi import HTTPException

from stt_vault.core.settings import Settings
from stt_vault.core.types import AssetRecord
from stt_vault.persistence import db
from stt_vault.processing.diarization import match_speakers


def clean_display_name(display_name: str) -> str:
    value = display_name.strip()
    if not value:
        raise HTTPException(status_code=400, detail="Display name is required")
    if len(value) > 120:
        raise HTTPException(status_code=400, detail="Display name is too long")
    return value


def resolve_speaker_id(
    settings: Settings,
    asset: AssetRecord,
    local_speaker: str,
    display_name: str,
) -> str:
    for segment in asset.get("transcript_segments") or []:
        if segment.get("speaker") != local_speaker:
            continue
        speaker_id = segment.get("speaker_id")
        if speaker_id and speaker_id != local_speaker:
            return speaker_id

    existing = db.find_speaker_by_display_name(settings.stt_db_path, display_name)
    if existing is not None:
        return existing["id"]

    return f"spk_{uuid4().hex[:12]}"


def count_local_speaker_segments(asset: AssetRecord, local_speaker: str) -> int:
    return max(
        1,
        sum(
            1
            for segment in asset.get("transcript_segments") or []
            if segment["speaker"] == local_speaker
        ),
    )


def recompute_asset_speaker_matches(settings: Settings, asset_ids: list[str]) -> list[str]:
    updated_asset_ids = []
    known_speakers = db.list_speakers(settings.stt_db_path)
    for asset_id in dict.fromkeys(asset_ids):
        asset = db.get_asset(settings.stt_db_path, asset_id)
        if asset is None:
            continue

        centroids = asset.get("speaker_centroids") or {}
        transcript_segments = asset.get("transcript_segments") or []
        if not centroids or not transcript_segments:
            continue

        matches = match_speakers(
            centroids,
            known_speakers,
            settings.speaker_similarity_threshold,
        )
        db.relabel_asset_speakers(settings.stt_db_path, asset_id, matches)
        updated_asset_ids.append(asset_id)
    return updated_asset_ids


__all__ = [
    "clean_display_name",
    "count_local_speaker_segments",
    "recompute_asset_speaker_matches",
    "resolve_speaker_id",
]
