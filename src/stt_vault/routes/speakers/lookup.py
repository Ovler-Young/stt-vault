from collections.abc import Mapping
from pathlib import Path

from fastapi import HTTPException

from stt_vault.core.api_models import SpeakerResponse
from stt_vault.persistence import db


def get_speaker_or_404(db_path: Path, speaker_id: str) -> SpeakerResponse:
    speaker = db.get_speaker(db_path, speaker_id)
    if speaker is None:
        raise HTTPException(status_code=404, detail="Speaker not found")
    return speaker_response(speaker)


def speaker_response(speaker: Mapping[str, object]) -> SpeakerResponse:
    return SpeakerResponse.model_validate(
        {
            "id": speaker.get("id"),
            "display_name": speaker.get("display_name"),
            "centroid": speaker.get("centroid"),
            "sample_count": speaker.get("sample_count"),
            "created_at": speaker.get("created_at"),
            "updated_at": speaker.get("updated_at"),
        }
    )
