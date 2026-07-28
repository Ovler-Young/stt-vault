from pathlib import Path

from stt_vault.core.models.records import (
    AssetRecord,
    ErrorRecord,
    EventPayload,
    TranscriptSegment,
    VisualEvent,
)

from .. import db


class SqliteWorkerRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def claim_next_job(self, owner: str, lease_seconds: int) -> str | None:
        return db.claim_next_job(self.db_path, owner, lease_seconds)

    def renew_job_claim(self, asset_id: str, owner: str, lease_seconds: int) -> bool:
        return db.renew_job_claim(self.db_path, asset_id, owner, lease_seconds)

    def mark_failed(self, asset_id: str, error: ErrorRecord) -> None:
        db.mark_failed(self.db_path, asset_id, error)

    def get_asset(self, asset_id: str) -> AssetRecord | None:
        return db.get_asset(self.db_path, asset_id, include_event_history=False)

    def list_transcript_chunks(self, asset_id: str) -> list[TranscriptSegment]:
        return db.list_transcript_chunks(self.db_path, asset_id)

    def update_stage(self, asset_id: str, stage: str) -> None:
        db.update_stage(self.db_path, asset_id, stage)

    def update_diarization_metadata(self, asset_id: str, **metadata: object) -> None:
        db.update_diarization_metadata(self.db_path, asset_id, **metadata)

    def reset_transcript_chunks(self, asset_id: str) -> None:
        db.reset_transcript_chunks(self.db_path, asset_id)

    def upsert_transcript_chunk(
        self, asset_id: str, index: int, result: TranscriptSegment, *, attempts: int
    ) -> None:
        db.upsert_transcript_chunk(self.db_path, asset_id, index, result, attempts=attempts)

    def list_speakers(self) -> list[dict[str, object]]:
        return db.list_speakers(self.db_path)

    def add_event(
        self,
        asset_id: str,
        level: str,
        stage: str,
        message: str,
        payload: EventPayload | ErrorRecord | None = None,
    ) -> None:
        db.add_event(self.db_path, asset_id, level, stage, message, payload)

    def update_progress(self, asset_id: str, **kwargs: int | None) -> None:
        db.update_progress(self.db_path, asset_id, **kwargs)

    def mark_success(self, asset_id: str, **values: object) -> None:
        db.mark_success(self.db_path, asset_id, **values)

    def mark_partial(self, asset_id: str, error: ErrorRecord) -> None:
        db.mark_partial(self.db_path, asset_id, error)

    def replace_visual_events(self, asset_id: str, events: list[VisualEvent]) -> None:
        db.replace_visual_events(self.db_path, asset_id, events)

    def update_asset_summary(self, asset_id: str, **values: str | None) -> None:
        db.update_asset_summary(self.db_path, asset_id, **values)

    def apply_ai_speaker_names(
        self, asset_id: str, speaker_names: dict[str, str]
    ) -> dict[str, str]:
        return db.apply_ai_speaker_names(self.db_path, asset_id, speaker_names)
