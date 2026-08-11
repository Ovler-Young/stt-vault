from fastapi import HTTPException

from stt_vault.core.models.api import SpeakerResponse
from stt_vault.core.models.records import SpeakerRecord
from stt_vault.persistence.sqlite_database import SqliteDatabase


def get_speaker_or_404(database: SqliteDatabase, speaker_id: str) -> SpeakerResponse:
    speaker = database.get_speaker(speaker_id)
    if speaker is None:
        raise HTTPException(status_code=404, detail="Speaker not found")
    return speaker_response(speaker)


def speaker_response(speaker: SpeakerRecord) -> SpeakerResponse:
    return SpeakerResponse(
        id=speaker.id,
        display_name=speaker.display_name,
        centroid=list(speaker.centroid),
        sample_count=speaker.sample_count,
        created_at=speaker.created_at,
        updated_at=speaker.updated_at,
    )
