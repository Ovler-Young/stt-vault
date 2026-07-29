from collections.abc import Mapping
from pathlib import Path

from stt_vault.core.models.api import JsonValue
from stt_vault.core.models.records import (
    AssetRecord,
    ErrorRecord,
    ExportPaths,
    KnownSpeaker,
    SpeakerSegment,
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

    def update_diarization_metadata(
        self,
        asset_id: str,
        *,
        wav_path: Path,
        duration: float,
        diarization_stats: dict[str, JsonValue],
        raw_segments: list[SpeakerSegment],
        merged_segments: list[SpeakerSegment],
        speaker_centroids: dict[str, list[float]],
    ) -> None:
        db.update_diarization_metadata(
            self.db_path,
            asset_id,
            wav_path=wav_path,
            duration=duration,
            diarization_stats=diarization_stats,
            raw_segments=raw_segments,
            merged_segments=merged_segments,
            speaker_centroids=speaker_centroids,
        )

    def reset_transcript_chunks(self, asset_id: str) -> None:
        db.reset_transcript_chunks(self.db_path, asset_id)

    def upsert_transcript_chunk(
        self, asset_id: str, index: int, result: TranscriptSegment, *, attempts: int
    ) -> None:
        db.upsert_transcript_chunk(self.db_path, asset_id, index, result, attempts=attempts)

    def list_speakers(self) -> list[KnownSpeaker]:
        return db.list_speakers(self.db_path)

    def add_event(
        self,
        asset_id: str,
        level: str,
        stage: str,
        message: str,
        payload: Mapping[str, JsonValue] | None = None,
    ) -> None:
        db.add_event(self.db_path, asset_id, level, stage, message, payload)

    def update_progress(
        self,
        asset_id: str,
        *,
        total_chunks: int | None = None,
        done_chunks: int | None = None,
        failed_chunks: int | None = None,
        next_retry_at: int | None = None,
    ) -> None:
        db.update_progress(
            self.db_path,
            asset_id,
            total_chunks=total_chunks,
            done_chunks=done_chunks,
            failed_chunks=failed_chunks,
            next_retry_at=next_retry_at,
        )

    def mark_success(
        self,
        asset_id: str,
        *,
        wav_path: Path,
        duration: float,
        diarization_stats: dict[str, JsonValue],
        raw_segments: list[SpeakerSegment],
        merged_segments: list[SpeakerSegment],
        speaker_centroids: dict[str, list[float]],
        transcript_segments: list[TranscriptSegment],
        exports: ExportPaths,
    ) -> None:
        db.mark_success(
            self.db_path,
            asset_id,
            wav_path=wav_path,
            duration=duration,
            diarization_stats=diarization_stats,
            raw_segments=raw_segments,
            merged_segments=merged_segments,
            speaker_centroids=speaker_centroids,
            transcript_segments=transcript_segments,
            exports=exports,
        )

    def mark_partial(self, asset_id: str, error: ErrorRecord) -> None:
        db.mark_partial(self.db_path, asset_id, error)

    def replace_visual_events(self, asset_id: str, events: list[VisualEvent]) -> None:
        db.replace_visual_events(self.db_path, asset_id, events)

    def update_asset_summary(
        self,
        asset_id: str,
        *,
        status: str,
        text: str | None = None,
        error: str | None = None,
        model: str | None = None,
        title: str | None = None,
    ) -> None:
        db.update_asset_summary(
            self.db_path,
            asset_id,
            status=status,
            text=text,
            error=error,
            model=model,
            title=title,
        )

    def apply_ai_speaker_names(
        self, asset_id: str, speaker_names: dict[str, str]
    ) -> dict[str, str]:
        return db.apply_ai_speaker_names(self.db_path, asset_id, speaker_names)
