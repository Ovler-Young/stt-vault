# ruff: noqa: E501
import fcntl
import hashlib
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from threading import RLock
from uuid import uuid4

from stt_vault.core.models.api import (
    FolderAssetSummary,
    FolderResponse,
    FolderTreeNodeResponse,
    FolderTreeResponse,
    TranscriptChunkRecord,
    UploadSessionResponse,
    VisualEventResponse,
)
from stt_vault.core.models.mod_contracts import EmbeddingSpaceV1
from stt_vault.core.models.persistence_errors import (
    AssetNotFoundError,
    DatabaseClosedError,
    EmbeddingSpaceConflictError,
    FolderDataIntegrityError,
    FolderNotFoundError,
    MigrationStateError,
    StaleClaimError,
)
from stt_vault.core.models.records import (
    AiSpeakerName,
    AppliedAiSpeakerNames,
    ApplyAiSpeakerNames,
    AssetCleanup,
    AssetMove,
    AssetMoveResult,
    AssetRecord,
    AssetSummaryUpdate,
    ClaimNextJob,
    ClaimRecoverableJobs,
    CleanupTask,
    CompleteAsset,
    CompleteDiarizationProviderInvocation,
    CompleteProviderRecovery,
    CompleteTranscriptionProviderInvocation,
    DiarizationMetadata,
    ErrorRecord,
    ExportPaths,
    FindProviderWorkItem,
    FolderCreate,
    FolderMove,
    FolderRename,
    JobClaim,
    JobEventCreate,
    JobEventRecord,
    JobOnlyRecoveryCommand,
    JobProgressUpdate,
    JobRecord,
    NewAsset,
    PersistedTimedTranscriptUnit,
    PersistedVisualEvent,
    PreparedProviderInvocation,
    PrepareProviderWorkItem,
    ProviderInvocationRecord,
    ProviderInvocationTransition,
    ProviderInvocationTransitionRecord,
    ProviderMetadata,
    ProviderRecoveryCommand,
    RecoveryClaimSet,
    RecoveryCompletion,
    RecoveryProviderEntry,
    RenewJobClaim,
    ReplaceTranscriptTimedUnits,
    RetryProviderInvocation,
    SpeakerRecord,
    SpeakerRelabel,
    SpeakerSegment,
    SpeakerUpsert,
    TranscriptChunkUpsert,
    TranscriptSegment,
    TransitionResult,
    UploadSessionCompletion,
    UploadSessionCreate,
    UploadSessionRecord,
    VisualEvent,
)
from stt_vault.core.speakers.names import is_local_speaker_label, is_usable_speaker_name

_ACTIVE_STATES = ("prepared", "sent", "accepted")
_TERMINAL_STATES = ("completed", "cancelled", "failed")
_LEGAL_TRANSITIONS = {
    "prepared": {"sent", "completed", "cancelled", "failed"},
    "sent": {"accepted", "cancelled", "failed"},
    "accepted": {"completed", "cancelled", "failed"},
    "completed": set(),
    "cancelled": set(),
    "failed": set(),
}
_RECORDED_AT_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})$")


@dataclass(frozen=True)
class _ColumnSpec:
    name: str
    column_type: str
    not_null: bool = False
    default: str | None = None
    primary_key_order: int = 0


@dataclass(frozen=True)
class _ForeignKeySpec:
    table: str
    from_column: str
    to_column: str
    on_delete: str


@dataclass(frozen=True)
class _IndexSpec:
    table: str
    columns: tuple[str, ...]
    unique: bool = False
    predicate: str | None = None


class SqliteDatabase:
    """The single SQLite implementation boundary for the application."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lifetime_lock = RLock()
        self._closed = False

    def initialize(self) -> None:
        with self._operation_guard(), self._schema_lock(), self._transaction() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (id TEXT PRIMARY KEY, applied_at INTEGER NOT NULL)"
            )
            self._run_migrations(conn)

    def close(self) -> None:
        with self._lifetime_lock:
            self._closed = True

    def create_asset(self, asset: NewAsset) -> NewAsset:
        timestamp = self._now()
        with self._transaction() as conn:
            conn.execute(
                """INSERT INTO assets (id, filename, recorded_at, media_type, parent_folder_id, original_path,
                status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)""",
                (
                    asset.asset_id,
                    asset.filename,
                    self._recorded_at_from_filename(asset.filename),
                    asset.media_type,
                    asset.parent_folder_id,
                    str(asset.original_path),
                    timestamp,
                    timestamp,
                ),
            )
            conn.execute(
                "INSERT INTO jobs (id, asset_id, status, created_at) VALUES (?, ?, 'queued', ?)",
                (asset.asset_id, asset.asset_id, timestamp),
            )
        return asset

    def claim_next_job(self, command: ClaimNextJob) -> JobClaim | None:
        if command.lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        timestamp = command.now if command.now is not None else self._now()
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT id, asset_id, run_attempt FROM jobs WHERE status = 'queued' ORDER BY created_at, id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            expires_at = timestamp + command.lease_seconds
            updated = conn.execute(
                """UPDATE jobs SET status = 'processing', stage = 'starting', started_at = ?,
                run_attempt = run_attempt + 1, claim_owner = ?, claim_expires_at = ?
                WHERE id = ? AND status = 'queued'""",
                (timestamp, command.claim_owner, expires_at, row["id"]),
            )
            if updated.rowcount != 1:
                return None
            conn.execute(
                "UPDATE assets SET status = 'processing', updated_at = ? WHERE id = ?",
                (timestamp, row["asset_id"]),
            )
            return JobClaim(
                str(row["asset_id"]), str(row["id"]), int(row["run_attempt"]) + 1, expires_at
            )

    def renew_job_claim(self, command: RenewJobClaim) -> bool:
        if isinstance(command.lease_seconds, bool) or command.lease_seconds < 1:
            raise ValueError("lease_seconds must be a positive integer")
        timestamp = command.now if command.now is not None else self._now()
        with self._transaction() as conn:
            result = conn.execute(
                """UPDATE jobs SET claim_expires_at = ? WHERE asset_id = ? AND status = 'processing'
                AND claim_owner = ? AND claim_expires_at > ?""",
                (
                    timestamp + command.lease_seconds,
                    command.asset_id,
                    command.claim_owner,
                    timestamp,
                ),
            )
        return result.rowcount == 1

    def get_job(self, asset_id: str) -> JobRecord | None:
        with self._read_connection() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE asset_id = ?", (asset_id,)).fetchone()
        return None if row is None else self._job_record(row)

    def list_jobs(self) -> list[JobRecord]:
        with self._read_connection() as conn:
            rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC, id DESC").fetchall()
        return [self._job_record(row) for row in rows]

    def get_active_job_context(self, asset_id: str) -> JobClaim | None:
        with self._read_connection() as conn:
            row = conn.execute(
                """SELECT id, asset_id, run_attempt, claim_expires_at FROM jobs
                WHERE asset_id = ? AND status = 'processing'""",
                (asset_id,),
            ).fetchone()
        if row is None or row["claim_expires_at"] is None:
            return None
        return JobClaim(
            str(row["asset_id"]),
            str(row["id"]),
            int(row["run_attempt"]),
            int(row["claim_expires_at"]),
        )

    def update_progress(self, command: JobProgressUpdate) -> None:
        assignments = ["next_retry_at = ?"]
        parameters: list[int | None | str] = [command.next_retry_at]
        for column, value in (
            ("progress_total_chunks", command.total_chunks),
            ("progress_done_chunks", command.done_chunks),
            ("progress_failed_chunks", command.failed_chunks),
        ):
            if value is not None:
                assignments.append(f"{column} = ?")
                parameters.append(value)
        parameters.append(command.asset_id)
        with self._transaction() as conn:
            conn.execute(f"UPDATE jobs SET {', '.join(assignments)} WHERE asset_id = ?", parameters)

    def add_event(self, command: JobEventCreate) -> None:
        timestamp = command.created_at if command.created_at is not None else self._now()
        with self._transaction() as conn:
            self._add_event_in_connection(
                conn,
                command.asset_id,
                command.level,
                command.stage,
                command.message,
                command.payload,
                timestamp,
            )

    def list_events(self, asset_id: str, *, limit: int = 200) -> list[JobEventRecord]:
        return self._list_events(asset_id, limit=limit)

    def list_current_run_events(self, asset_id: str, *, limit: int = 200) -> list[JobEventRecord]:
        job = self.get_job(asset_id)
        return (
            []
            if job is None
            else self._list_events(asset_id, limit=limit, run_attempt=job.run_attempt)
        )

    def update_stage(self, *, asset_id: str, stage: str) -> None:
        with self._transaction() as conn:
            conn.execute("UPDATE jobs SET stage = ? WHERE asset_id = ?", (stage, asset_id))
            conn.execute("UPDATE assets SET updated_at = ? WHERE id = ?", (self._now(), asset_id))
            self._add_event_in_connection(conn, asset_id, "info", stage, stage, None, self._now())

    def complete_asset(self, command: CompleteAsset) -> None:
        timestamp = self._now()
        with self._transaction() as conn:
            conn.execute(
                """UPDATE assets SET status = 'success', wav_path = ?, duration = ?, diarization_stats = ?, raw_segments = ?, merged_segments = ?, speaker_centroids = ?, transcript_segments = ?, exports = ?, updated_at = ? WHERE id = ?""",
                (
                    str(command.metadata.wav_path),
                    command.metadata.duration,
                    json.dumps(command.metadata.diarization_stats.as_dict()),
                    json.dumps(
                        [
                            self._speaker_segment_payload(item)
                            for item in command.metadata.raw_segments
                        ]
                    ),
                    json.dumps(
                        [
                            self._speaker_segment_payload(item)
                            for item in command.metadata.merged_segments
                        ]
                    ),
                    json.dumps(command.metadata.speaker_centroids.as_dict()),
                    json.dumps(
                        [
                            self._transcript_segment_payload(item)
                            for item in command.transcript_segments
                        ]
                    ),
                    json.dumps(self._export_payload(command.exports)),
                    timestamp,
                    command.asset_id,
                ),
            )
            conn.execute(
                """UPDATE jobs SET status = 'success', stage = 'done', finished_at = ?, claim_owner = NULL, claim_expires_at = NULL WHERE asset_id = ?""",
                (timestamp, command.asset_id),
            )
            self._add_event_in_connection(
                conn, command.asset_id, "info", "done", "Job completed", None, timestamp
            )

    def mark_failed(self, asset_id: str, error: ErrorRecord, *, partial: bool = False) -> None:
        timestamp, status = self._now(), "partial" if partial else "failed"
        payload = json.dumps(self._error_payload(error))
        with self._transaction() as conn:
            conn.execute(
                """UPDATE jobs SET status = ?, error = ?, finished_at = ?, claim_owner = NULL, claim_expires_at = NULL WHERE asset_id = ?""",
                (status, payload, timestamp, asset_id),
            )
            conn.execute(
                "UPDATE assets SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                (status, payload, timestamp, asset_id),
            )
            message = error.message or "Asset processing failed"
            self._add_event_in_connection(
                conn, asset_id, "error", status, message, error, timestamp
            )

    def mark_partial(self, asset_id: str, error: ErrorRecord) -> None:
        self.mark_failed(asset_id, error, partial=True)

    def list_assets(self) -> list[AssetRecord]:
        with self._read_connection() as conn:
            rows = conn.execute(
                """SELECT id, filename, title, recorded_at, media_type, duration, status, error,
                summary_status, parent_folder_id, created_at, updated_at FROM assets
                ORDER BY recorded_at IS NULL, recorded_at DESC, created_at DESC, id DESC"""
            ).fetchall()
        return [self._asset_record(row) for row in rows]

    def get_asset(self, asset_id: str, *, include_event_history: bool = True) -> AssetRecord | None:
        with self._read_connection() as conn:
            row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
            if row is None:
                return None
            asset = self._asset_record(row)
            chunks = self._list_transcript_chunks_in_connection(conn, asset_id)
            if chunks:
                units_by_chunk: dict[int, list[PersistedTimedTranscriptUnit]] = {}
                for unit in self._list_transcript_timed_units_in_connection(conn, asset_id):
                    units_by_chunk.setdefault(unit.chunk_index, []).append(unit)
                asset = replace(
                    asset,
                    transcript_segments=tuple(
                        replace(chunk, timed_units=tuple(units_by_chunk.get(chunk.chunk_index, ())))
                        for chunk in chunks
                    ),
                )
            job = conn.execute("SELECT * FROM jobs WHERE asset_id = ?", (asset_id,)).fetchone()
            job_record = self._job_record(job) if job is not None else None
            events = (
                tuple(
                    self._list_events_in_connection(
                        conn, asset_id, limit=200, run_attempt=job_record.run_attempt
                    )
                )
                if job_record is not None
                else None
            )
            event_history = (
                tuple(self._list_events_in_connection(conn, asset_id, limit=200))
                if include_event_history
                else None
            )
            visual_events = tuple(self._list_visual_events_in_connection(conn, asset_id))
            asset = replace(
                asset,
                job=job_record,
                events=events,
                event_history=event_history,
                visual_events=visual_events,
            )
        return asset

    def asset_exists(self, asset_id: str) -> bool:
        with self._read_connection() as conn:
            return (
                conn.execute("SELECT 1 FROM assets WHERE id = ?", (asset_id,)).fetchone()
                is not None
            )

    def move_asset(self, command: AssetMove) -> AssetMoveResult:
        timestamp = self._now()
        with self._transaction() as conn:
            if (
                conn.execute("SELECT 1 FROM assets WHERE id = ?", (command.asset_id,)).fetchone()
                is None
            ):
                raise AssetNotFoundError(command.asset_id)
            self._required_folder(conn, command.parent_folder_id)
            conn.execute(
                "UPDATE assets SET parent_folder_id = ?, updated_at = ? WHERE id = ?",
                (command.parent_folder_id, timestamp, command.asset_id),
            )
        return AssetMoveResult(command.asset_id, command.parent_folder_id, timestamp)

    def record_cleanup_task(self, command: AssetCleanup) -> None:
        with self._transaction() as conn:
            self._upsert_cleanup_task(conn, command)

    def delete_asset_with_cleanup_task(self, command: AssetCleanup) -> None:
        with self._transaction() as conn:
            if (
                conn.execute("SELECT 1 FROM assets WHERE id = ?", (command.asset_id,)).fetchone()
                is None
            ):
                raise AssetNotFoundError(command.asset_id)
            self._upsert_cleanup_task(conn, command)
            conn.execute("DELETE FROM assets WHERE id = ?", (command.asset_id,))

    def get_cleanup_task(self, asset_id: str) -> CleanupTask | None:
        with self._read_connection() as conn:
            row = conn.execute(
                "SELECT asset_id, media_path, exports_path FROM asset_cleanup_tasks WHERE asset_id = ?",
                (asset_id,),
            ).fetchone()
        if row is None:
            return None
        return CleanupTask(
            asset_id=str(row["asset_id"]),
            media_path=str(row["media_path"]),
            exports_path=str(row["exports_path"]),
        )

    def clear_cleanup_task(self, asset_id: str) -> None:
        with self._transaction() as conn:
            conn.execute("DELETE FROM asset_cleanup_tasks WHERE asset_id = ?", (asset_id,))

    def update_diarization_metadata(self, command: DiarizationMetadata) -> None:
        with self._transaction() as conn:
            conn.execute(
                """UPDATE assets SET wav_path = ?, duration = ?, diarization_stats = ?, raw_segments = ?,
                merged_segments = ?, speaker_centroids = ?, embedding_space = ?, updated_at = ? WHERE id = ?""",
                (
                    str(command.wav_path),
                    command.duration,
                    json.dumps(command.diarization_stats.as_dict()),
                    json.dumps(
                        [self._speaker_segment_payload(item) for item in command.raw_segments]
                    ),
                    json.dumps(
                        [self._speaker_segment_payload(item) for item in command.merged_segments]
                    ),
                    json.dumps(command.speaker_centroids.as_dict()),
                    command.embedding_space.model_dump_json() if command.embedding_space else None,
                    self._now(),
                    command.asset_id,
                ),
            )

    def update_asset_exports(self, asset_id: str, exports: ExportPaths) -> None:
        with self._transaction() as conn:
            conn.execute(
                "UPDATE assets SET exports = ?, updated_at = ? WHERE id = ?",
                (json.dumps(self._export_payload(exports)), self._now(), asset_id),
            )

    def update_asset_summary(self, command: AssetSummaryUpdate) -> None:
        timestamp = self._now()
        with self._transaction() as conn:
            conn.execute(
                """UPDATE assets SET summary_status = ?, summary_text = ?, summary_error = ?, summary_model = ?,
                title = COALESCE(?, title), summary_updated_at = ?, updated_at = ? WHERE id = ?""",
                (
                    command.status,
                    command.text,
                    command.error,
                    command.model,
                    command.title,
                    timestamp,
                    timestamp,
                    command.asset_id,
                ),
            )

    def retry_asset(self, asset_id: str) -> None:
        timestamp = self._now()
        with self._transaction() as conn:
            if conn.execute("SELECT 1 FROM assets WHERE id = ?", (asset_id,)).fetchone() is None:
                raise AssetNotFoundError(asset_id)
            job = conn.execute(
                "SELECT id, run_attempt FROM jobs WHERE asset_id = ?", (asset_id,)
            ).fetchone()
            if job is None:
                raise KeyError(asset_id)
            next_run_attempt = int(job["run_attempt"]) + 1
            conn.execute(
                "UPDATE assets SET status = 'queued', error = NULL, updated_at = ? WHERE id = ?",
                (timestamp, asset_id),
            )
            conn.execute(
                """UPDATE jobs SET status = 'queued', stage = NULL, error = NULL, started_at = NULL,
            finished_at = NULL, progress_total_chunks = 0, progress_done_chunks = 0, progress_failed_chunks = 0,
            next_retry_at = NULL, claim_owner = NULL, claim_expires_at = NULL,
            provider_work_generation = provider_work_generation + 1 WHERE asset_id = ?""",
                (asset_id,),
            )
            self._add_event_in_connection(
                conn,
                asset_id,
                "info",
                "queued",
                "Job queued for retry",
                None,
                timestamp,
                next_run_attempt,
            )

    def reset_transcript_chunks(self, asset_id: str) -> None:
        with self._transaction() as conn:
            conn.execute("DELETE FROM transcript_chunks WHERE asset_id = ?", (asset_id,))
            self._sync_transcript_cache(conn, asset_id, self._now())

    def upsert_transcript_chunk(self, command: TranscriptChunkUpsert) -> None:
        with self._transaction() as conn:
            self._upsert_transcript_chunk(conn, command, self._now())

    def list_transcript_chunks(self, asset_id: str) -> list[TranscriptSegment]:
        with self._read_connection() as conn:
            return self._list_transcript_chunks_in_connection(conn, asset_id)

    def list_transcript_timed_units(self, asset_id: str) -> list[PersistedTimedTranscriptUnit]:
        with self._read_connection() as conn:
            return self._list_transcript_timed_units_in_connection(conn, asset_id)

    def replace_transcript_timed_units(
        self, command: ReplaceTranscriptTimedUnits
    ) -> list[PersistedTimedTranscriptUnit]:
        with self._transaction() as conn:
            self._replace_transcript_timed_units_in_connection(conn, command)
            return self._list_transcript_timed_units_in_connection(conn, command.asset_id)

    def apply_speaker_name_updates(self, command: ApplyAiSpeakerNames) -> AppliedAiSpeakerNames:
        timestamp = self._now()
        applied: list[AiSpeakerName] = []
        cached_segments_updated = False
        with self._transaction() as conn:
            for name in command.names:
                local_speaker, display_name = name.local_speaker, name.display_name
                if not (
                    is_local_speaker_label(local_speaker) and is_usable_speaker_name(display_name)
                ):
                    continue
                result = conn.execute(
                    """UPDATE transcript_chunks SET speaker_name = ?, updated_at = ? WHERE asset_id = ?
                    AND speaker = ? AND (speaker_name IS NULL OR trim(speaker_name) = '' OR speaker_name = speaker)""",
                    (display_name.strip(), timestamp, command.asset_id, local_speaker),
                )
                if result.rowcount:
                    applied.append(AiSpeakerName(local_speaker, display_name.strip()))
                    continue
                cached_segments = self._cached_transcript_segments(conn, command.asset_id)
                updated_segments = tuple(
                    replace(segment, speaker_name=display_name.strip())
                    if segment.speaker == local_speaker
                    and (
                        segment.speaker_name is None
                        or not segment.speaker_name.strip()
                        or segment.speaker_name == segment.speaker
                    )
                    else segment
                    for segment in cached_segments
                )
                if updated_segments != cached_segments:
                    conn.execute(
                        "UPDATE assets SET transcript_segments = ?, updated_at = ? WHERE id = ?",
                        (
                            json.dumps(
                                [
                                    self._transcript_segment_payload(segment)
                                    for segment in updated_segments
                                ]
                            ),
                            timestamp,
                            command.asset_id,
                        ),
                    )
                    applied.append(AiSpeakerName(local_speaker, display_name.strip()))
                    cached_segments_updated = True
            if applied and not cached_segments_updated:
                self._sync_transcript_cache(conn, command.asset_id, timestamp)
        return AppliedAiSpeakerNames(tuple(applied))

    def replace_visual_events(self, asset_id: str, events: list[VisualEvent]) -> None:
        timestamp = self._now()
        with self._transaction() as conn:
            conn.execute("DELETE FROM asset_visual_events WHERE asset_id = ?", (asset_id,))
            conn.executemany(
                """INSERT INTO asset_visual_events (asset_id, event_index, timestamp, score, kind, created_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (
                        asset_id,
                        index,
                        event.timestamp,
                        event.score,
                        event.kind,
                        timestamp,
                    )
                    for index, event in enumerate(events)
                ],
            )

    def list_visual_events(self, asset_id: str) -> list[PersistedVisualEvent]:
        with self._read_connection() as conn:
            return self._list_visual_events_in_connection(conn, asset_id)

    def list_speakers(self) -> list[SpeakerRecord]:
        with self._read_connection() as conn:
            rows = conn.execute("SELECT * FROM speakers ORDER BY display_name").fetchall()
        return [self._known_speaker(row) for row in rows]

    def get_speaker(self, speaker_id: str) -> SpeakerRecord | None:
        with self._read_connection() as conn:
            row = conn.execute("SELECT * FROM speakers WHERE id = ?", (speaker_id,)).fetchone()
        return self._speaker_record(row) if row is not None else None

    def find_speaker_by_display_name(self, display_name: str) -> SpeakerRecord | None:
        with self._read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM speakers WHERE lower(display_name) = lower(?)", (display_name,)
            ).fetchone()
        return self._speaker_record(row) if row is not None else None

    def list_asset_ids_with_speaker_centroids(self) -> list[str]:
        with self._read_connection() as conn:
            rows = conn.execute(
                "SELECT id FROM assets WHERE speaker_centroids IS NOT NULL ORDER BY updated_at DESC"
            ).fetchall()
        return [str(row["id"]) for row in rows]

    def upsert_speaker(self, command: SpeakerUpsert) -> None:
        timestamp = self._now()
        centroid = command.centroid
        sample_count = command.sample_count
        with self._transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM speakers WHERE id = ?", (command.speaker_id,)
            ).fetchone()
            if existing is not None:
                prior = self._speaker_record(existing)
                if prior.embedding_space != command.embedding_space:
                    raise EmbeddingSpaceConflictError("Speaker embedding space is incompatible")
                prior_count = max(1, prior.sample_count)
                incoming_count = max(1, sample_count)
                if len(prior.centroid) == len(centroid):
                    sample_count = prior_count + incoming_count
                    centroid = tuple(
                        ((old * prior_count) + (new * incoming_count)) / sample_count
                        for old, new in zip(prior.centroid, centroid, strict=False)
                    )
            conn.execute(
                """INSERT INTO speakers (id, display_name, centroid, sample_count, embedding_space, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET display_name = excluded.display_name,
                centroid = excluded.centroid, sample_count = excluded.sample_count,
                embedding_space = excluded.embedding_space, updated_at = excluded.updated_at""",
                (
                    command.speaker_id,
                    command.display_name,
                    json.dumps(centroid),
                    sample_count,
                    command.embedding_space.model_dump_json(),
                    timestamp,
                    timestamp,
                ),
            )

    def rename_speaker(self, speaker_id: str, display_name: str) -> None:
        timestamp = self._now()
        with self._transaction() as conn:
            conn.execute(
                "UPDATE speakers SET display_name = ?, updated_at = ? WHERE id = ?",
                (display_name, timestamp, speaker_id),
            )
            conn.execute(
                "UPDATE transcript_chunks SET speaker_name = ?, updated_at = ? WHERE speaker_id = ?",
                (display_name, timestamp, speaker_id),
            )
            self._refresh_speaker_transcript_caches(conn, speaker_id, timestamp)

    def merge_speakers(self, source_speaker_id: str, target_speaker_id: str) -> None:
        if source_speaker_id == target_speaker_id:
            return
        timestamp = self._now()
        with self._transaction() as conn:
            source = conn.execute(
                "SELECT * FROM speakers WHERE id = ?", (source_speaker_id,)
            ).fetchone()
            target = conn.execute(
                "SELECT * FROM speakers WHERE id = ?", (target_speaker_id,)
            ).fetchone()
            if source is None or target is None:
                raise KeyError(source_speaker_id if source is None else target_speaker_id)
            source_record, target_record = (
                self._speaker_record(source),
                self._speaker_record(target),
            )
            if source_record.embedding_space != target_record.embedding_space:
                raise EmbeddingSpaceConflictError("Speaker embedding space is incompatible")
            source_count, target_count = (
                max(1, source_record.sample_count),
                max(1, target_record.sample_count),
            )
            merged_count = source_count + target_count
            centroid = list(target_record.centroid)
            if len(source_record.centroid) == len(centroid):
                centroid = [
                    ((target_value * target_count) + (source_value * source_count)) / merged_count
                    for target_value, source_value in zip(
                        centroid, source_record.centroid, strict=False
                    )
                ]
            rows = conn.execute(
                "SELECT DISTINCT asset_id FROM transcript_chunks WHERE speaker_id IN (?, ?)",
                (source_speaker_id, target_speaker_id),
            ).fetchall()
            conn.execute(
                "UPDATE speakers SET centroid = ?, sample_count = ?, updated_at = ? WHERE id = ?",
                (json.dumps(centroid), merged_count, timestamp, target_speaker_id),
            )
            conn.execute("DELETE FROM speakers WHERE id = ?", (source_speaker_id,))
            conn.execute(
                "UPDATE transcript_chunks SET speaker_id = ?, speaker_name = ?, updated_at = ? WHERE speaker_id = ?",
                (target_speaker_id, str(target["display_name"]), timestamp, source_speaker_id),
            )
            for row in rows:
                self._sync_transcript_cache(conn, str(row["asset_id"]), timestamp)

    def delete_speaker(self, speaker_id: str) -> None:
        timestamp = self._now()
        with self._transaction() as conn:
            rows = conn.execute(
                "SELECT DISTINCT asset_id FROM transcript_chunks WHERE speaker_id = ?",
                (speaker_id,),
            ).fetchall()
            conn.execute("DELETE FROM speakers WHERE id = ?", (speaker_id,))
            conn.execute(
                """UPDATE transcript_chunks SET speaker_id = speaker, speaker_name = speaker,
            speaker_similarity = NULL, updated_at = ? WHERE speaker_id = ?""",
                (timestamp, speaker_id),
            )
            for row in rows:
                self._sync_transcript_cache(conn, str(row["asset_id"]), timestamp)

    def relabel_asset_speaker(self, command: SpeakerRelabel) -> None:
        timestamp = self._now()
        with self._transaction() as conn:
            conn.execute(
                """UPDATE transcript_chunks SET speaker_id = ?, speaker_name = ?, speaker_similarity = ?, updated_at = ?
            WHERE asset_id = ? AND speaker = ?""",
                (
                    command.speaker_id,
                    command.display_name,
                    command.similarity,
                    timestamp,
                    command.asset_id,
                    command.local_speaker,
                ),
            )
            self._sync_transcript_cache(conn, command.asset_id, timestamp)

    def list_asset_ids_for_speaker(self, speaker_id: str) -> list[str]:
        with self._read_connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT asset_id FROM transcript_chunks WHERE speaker_id = ?",
                (speaker_id,),
            ).fetchall()
        return [str(row["asset_id"]) for row in rows]

    def create_folder(self, command: FolderCreate) -> FolderResponse:
        name, folder_id, timestamp = (
            self._normalize_folder_name(command.name),
            uuid4().hex,
            self._now(),
        )
        with self._transaction() as conn:
            self._required_folder(conn, command.parent_id)
            conn.execute(
                "INSERT INTO folders (id, name, parent_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (folder_id, name, command.parent_id, timestamp, timestamp),
            )
        return FolderResponse(
            id=folder_id,
            name=name,
            parent_id=command.parent_id,
            created_at=timestamp,
            updated_at=timestamp,
        )

    def get_folder(self, folder_id: str) -> FolderResponse | None:
        with self._read_connection() as conn:
            row = conn.execute(
                "SELECT id, name, parent_id, created_at, updated_at FROM folders WHERE id = ?",
                (folder_id,),
            ).fetchone()
        return self._folder(row) if row is not None else None

    def list_folders(self) -> list[FolderResponse]:
        with self._read_connection() as conn:
            rows = conn.execute(
                "SELECT id, name, parent_id, created_at, updated_at FROM folders ORDER BY created_at ASC, id ASC"
            ).fetchall()
        return [self._folder(row) for row in rows]

    def move_folder(self, command: FolderMove) -> FolderResponse:
        timestamp = self._now()
        with self._transaction() as conn:
            folder = self._required_folder(conn, command.folder_id)
            self._required_folder(conn, command.parent_id)
            if command.folder_id == command.parent_id:
                raise ValueError("A folder cannot be moved into itself")
            if command.parent_id is not None and self._folder_is_descendant(
                conn, command.folder_id, command.parent_id
            ):
                raise ValueError("A folder cannot be moved into a descendant")
            conn.execute(
                "UPDATE folders SET parent_id = ?, updated_at = ? WHERE id = ?",
                (command.parent_id, timestamp, command.folder_id),
            )
        return folder.model_copy(update={"parent_id": command.parent_id, "updated_at": timestamp})

    def rename_folder(self, command: FolderRename) -> FolderResponse:
        name, timestamp = self._normalize_folder_name(command.name), self._now()
        with self._transaction() as conn:
            folder = self._required_folder(conn, command.folder_id)
            conn.execute(
                "UPDATE folders SET name = ?, updated_at = ? WHERE id = ?",
                (name, timestamp, command.folder_id),
            )
        return folder.model_copy(update={"name": name, "updated_at": timestamp})

    def delete_folder(self, folder_id: str) -> None:
        with self._transaction() as conn:
            self._required_folder(conn, folder_id)
            has_child = conn.execute(
                "SELECT 1 FROM folders WHERE parent_id = ? LIMIT 1", (folder_id,)
            ).fetchone()
            has_asset = conn.execute(
                "SELECT 1 FROM assets WHERE parent_folder_id = ? LIMIT 1", (folder_id,)
            ).fetchone()
            if has_child is not None or has_asset is not None:
                raise ValueError("Folder is not empty")
            conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))

    def list_folder_tree(self) -> FolderTreeResponse:
        with self._read_connection() as conn:
            folders = [
                self._folder(row)
                for row in conn.execute(
                    "SELECT id, name, parent_id, created_at, updated_at FROM folders ORDER BY created_at ASC, id ASC"
                ).fetchall()
            ]
            assets = [
                self._folder_asset(row)
                for row in conn.execute(
                    """SELECT id, filename, title, recorded_at, media_type, duration, status, error, summary_status, parent_folder_id, created_at, updated_at FROM assets ORDER BY recorded_at IS NULL, recorded_at DESC, created_at DESC, id DESC"""
                ).fetchall()
            ]
        by_id = {
            folder.id: FolderTreeNodeResponse(**folder.model_dump(), children=[], assets=[])
            for folder in folders
        }
        roots: list[FolderTreeNodeResponse] = []
        for folder in folders:
            node = by_id[folder.id]
            if folder.parent_id is None:
                roots.append(node)
            elif (parent := by_id.get(folder.parent_id)) is not None:
                parent.children.append(node)
            else:
                raise FolderDataIntegrityError("Folder references an unknown parent")
        self._validate_folder_tree(roots, set(by_id))
        root_assets: list[FolderAssetSummary] = []
        for asset in assets:
            if asset.parent_folder_id is None:
                root_assets.append(asset)
            elif (parent := by_id.get(asset.parent_folder_id)) is not None:
                parent.assets.append(asset)
            else:
                raise FolderDataIntegrityError("Asset references an unknown folder")
        return FolderTreeResponse(folders=roots, assets=root_assets)

    def create_upload_session(self, command: UploadSessionCreate) -> UploadSessionRecord:
        upload_id, timestamp = uuid4().hex, self._now()
        temp_path = command.uploads_dir / f"{upload_id}.part"
        with self._transaction() as conn:
            conn.execute(
                """INSERT INTO upload_sessions (id, filename, total_size, offset, temp_path, created_at, updated_at)
            VALUES (?, ?, ?, 0, ?, ?, ?)""",
                (
                    upload_id,
                    command.filename,
                    command.total_size,
                    str(temp_path),
                    timestamp,
                    timestamp,
                ),
            )
        return UploadSessionRecord(
            id=upload_id,
            filename=command.filename,
            total_size=command.total_size,
            offset=0,
            temp_path=str(temp_path),
            created_at=timestamp,
            updated_at=timestamp,
        )

    def get_upload_session(self, upload_id: str) -> UploadSessionRecord | None:
        with self._read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM upload_sessions WHERE id = ?", (upload_id,)
            ).fetchone()
        if row is None:
            return None
        result = UploadSessionResponse.model_validate(dict(row))
        return UploadSessionRecord(**result.model_dump())

    def update_upload_offset(self, upload_id: str, offset: int) -> None:
        with self._transaction() as conn:
            conn.execute(
                "UPDATE upload_sessions SET offset = ?, updated_at = ? WHERE id = ?",
                (offset, self._now(), upload_id),
            )

    def delete_upload_session(self, upload_id: str) -> None:
        with self._transaction() as conn:
            conn.execute("DELETE FROM upload_sessions WHERE id = ?", (upload_id,))

    def complete_upload_session(self, command: UploadSessionCompletion) -> None:
        timestamp = self._now()
        with self._transaction() as conn:
            upload = conn.execute(
                "SELECT filename FROM upload_sessions WHERE id = ?", (command.upload_id,)
            ).fetchone()
            if upload is None:
                raise KeyError(command.upload_id)
            self._create_asset_in_connection(
                conn,
                NewAsset(
                    command.asset_id,
                    str(upload["filename"]),
                    command.media_type,
                    command.stored_path,
                ),
                timestamp,
            )
            conn.execute("DELETE FROM upload_sessions WHERE id = ?", (command.upload_id,))

    _MIGRATION_IDS = (
        "H0001_assets",
        "H0002_folders",
        "H0003_speakers",
        "H0004_jobs_and_claim_columns",
        "H0005_job_events_run_attempt",
        "H0006_transcript_chunks",
        "H0007_asset_metadata_columns",
        "H0008_upload_sessions",
        "H0009_historical_indexes",
        "D0008_001_provider_ledger_tables",
        "D0008_002_embedding_space_columns",
        "D0008_003_provider_ledger_indexes_and_triggers",
        "D0008_004_timed_transcript_units",
    )

    def _run_migrations(self, conn: sqlite3.Connection) -> None:
        recorded = [str(row["id"]) for row in conn.execute("SELECT id FROM schema_migrations")]
        unknown = set(recorded) - set(self._MIGRATION_IDS)
        if unknown:
            raise MigrationStateError(
                f"schema migration history contains unknown IDs: {sorted(unknown)}"
            )
        recorded_set = set(recorded)
        expected_prefix = self._MIGRATION_IDS[: len(recorded_set)]
        if recorded_set != set(expected_prefix):
            raise MigrationStateError("schema migration history is not an ordered prefix")
        provider_tables = self._provider_table_names(conn)
        if provider_tables and not recorded_set.intersection(self._MIGRATION_IDS[9:]):
            self._validate_provider_ledger_adoption_preconditions(conn)
        for migration_id in self._MIGRATION_IDS:
            if migration_id in recorded_set:
                self._verify_migration(conn, migration_id)
                continue
            self._apply_migration(conn, migration_id)
            self._verify_migration(conn, migration_id)
            conn.execute(
                "INSERT INTO schema_migrations (id, applied_at) VALUES (?, ?)",
                (migration_id, self._now()),
            )

    def _apply_migration(self, conn: sqlite3.Connection, migration_id: str) -> None:
        if migration_id == "H0001_assets":
            conn.execute(
                "CREATE TABLE IF NOT EXISTS assets (id TEXT PRIMARY KEY, filename TEXT NOT NULL, media_type TEXT NOT NULL, original_path TEXT NOT NULL, status TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)"
            )
        elif migration_id == "H0002_folders":
            conn.execute(
                "CREATE TABLE IF NOT EXISTS folders (id TEXT PRIMARY KEY, name TEXT NOT NULL, parent_id TEXT REFERENCES folders(id) ON DELETE RESTRICT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)"
            )
        elif migration_id == "H0003_speakers":
            conn.execute(
                "CREATE TABLE IF NOT EXISTS speakers (id TEXT PRIMARY KEY, display_name TEXT NOT NULL, centroid TEXT NOT NULL, sample_count INTEGER NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)"
            )
        elif migration_id == "H0004_jobs_and_claim_columns":
            conn.execute(
                "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE, status TEXT NOT NULL, stage TEXT, run_attempt INTEGER NOT NULL DEFAULT 0, claim_owner TEXT, claim_expires_at INTEGER, created_at INTEGER NOT NULL, started_at INTEGER, finished_at INTEGER)"
            )
            self._add_columns(
                conn,
                "jobs",
                {
                    "stage": "TEXT",
                    "run_attempt": "INTEGER NOT NULL DEFAULT 0",
                    "claim_owner": "TEXT",
                    "claim_expires_at": "INTEGER",
                    "started_at": "INTEGER",
                    "finished_at": "INTEGER",
                },
            )
        elif migration_id == "H0005_job_events_run_attempt":
            conn.execute(
                "CREATE TABLE IF NOT EXISTS job_events (id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE, asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE, level TEXT NOT NULL, stage TEXT, message TEXT NOT NULL, payload TEXT, run_attempt INTEGER DEFAULT 0, created_at INTEGER NOT NULL)"
            )
            self._add_columns(conn, "job_events", {"run_attempt": "INTEGER DEFAULT 0"})
        elif migration_id == "H0006_transcript_chunks":
            conn.execute(
                "CREATE TABLE IF NOT EXISTS transcript_chunks (asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE, chunk_index INTEGER NOT NULL, start REAL NOT NULL, end REAL NOT NULL, chunk_start REAL NOT NULL, chunk_end REAL NOT NULL, speaker TEXT NOT NULL, speaker_id TEXT, speaker_name TEXT, speaker_similarity REAL, text TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL, error TEXT, updated_at INTEGER NOT NULL, PRIMARY KEY(asset_id, chunk_index))"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS asset_visual_events (asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE, event_index INTEGER NOT NULL, timestamp REAL NOT NULL, score REAL NOT NULL, kind TEXT NOT NULL, created_at INTEGER NOT NULL, PRIMARY KEY(asset_id, event_index))"
            )
        elif migration_id == "H0007_asset_metadata_columns":
            self._add_columns(
                conn,
                "assets",
                {
                    "parent_folder_id": "TEXT REFERENCES folders(id) ON DELETE SET NULL",
                    "title": "TEXT",
                    "recorded_at": "INTEGER",
                    "wav_path": "TEXT",
                    "duration": "REAL",
                    "error": "TEXT",
                    "diarization_stats": "TEXT",
                    "raw_segments": "TEXT",
                    "merged_segments": "TEXT",
                    "speaker_centroids": "TEXT",
                    "transcript_segments": "TEXT",
                    "exports": "TEXT",
                    "summary_status": "TEXT",
                    "summary_text": "TEXT",
                    "summary_error": "TEXT",
                    "summary_model": "TEXT",
                    "summary_updated_at": "INTEGER",
                },
            )
            self._add_columns(
                conn,
                "jobs",
                {
                    "error": "TEXT",
                    "progress_total_chunks": "INTEGER DEFAULT 0",
                    "progress_done_chunks": "INTEGER DEFAULT 0",
                    "progress_failed_chunks": "INTEGER DEFAULT 0",
                    "next_retry_at": "INTEGER",
                    "provider_work_generation": "INTEGER NOT NULL DEFAULT 1",
                },
            )
            for row in conn.execute(
                "SELECT id, filename FROM assets WHERE recorded_at IS NULL"
            ).fetchall():
                recorded_at = self._recorded_at_from_filename(str(row["filename"]))
                if recorded_at is not None:
                    conn.execute(
                        "UPDATE assets SET recorded_at = ? WHERE id = ?", (recorded_at, row["id"])
                    )
        elif migration_id == "H0008_upload_sessions":
            conn.execute(
                "CREATE TABLE IF NOT EXISTS asset_cleanup_tasks (asset_id TEXT PRIMARY KEY, media_path TEXT NOT NULL, exports_path TEXT NOT NULL, created_at INTEGER NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS upload_sessions (id TEXT PRIMARY KEY, filename TEXT NOT NULL, total_size INTEGER NOT NULL, offset INTEGER NOT NULL DEFAULT 0, temp_path TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)"
            )
        elif migration_id == "H0009_historical_indexes":
            for statement in (
                "CREATE INDEX IF NOT EXISTS idx_assets_created_at ON assets(created_at)",
                "CREATE INDEX IF NOT EXISTS idx_assets_parent_folder_id ON assets(parent_folder_id)",
                "CREATE INDEX IF NOT EXISTS idx_assets_recorded_at ON assets(recorded_at DESC)",
                "CREATE INDEX IF NOT EXISTS idx_folders_parent_id ON folders(parent_id)",
                "CREATE INDEX IF NOT EXISTS idx_jobs_status_created_at ON jobs(status, created_at)",
                "CREATE INDEX IF NOT EXISTS idx_jobs_processing_claim ON jobs(status, claim_expires_at)",
                "CREATE INDEX IF NOT EXISTS idx_job_events_asset_created_at ON job_events(asset_id, created_at)",
                "CREATE INDEX IF NOT EXISTS idx_transcript_chunks_asset_index ON transcript_chunks(asset_id, chunk_index)",
                "CREATE INDEX IF NOT EXISTS idx_visual_events_asset_index ON asset_visual_events(asset_id, event_index)",
            ):
                conn.execute(statement)
        elif migration_id == "D0008_001_provider_ledger_tables":
            self._apply_provider_ledger_tables(conn)
        elif migration_id == "D0008_002_embedding_space_columns":
            self._add_columns(conn, "assets", {"embedding_space": "TEXT"})
            self._add_columns(conn, "speakers", {"embedding_space": "TEXT"})
            self._add_columns(conn, "provider_work_items", {"completed_at": "INTEGER"})
            self._add_columns(
                conn,
                "provider_invocations",
                {
                    "duplicate_recovery": "INTEGER NOT NULL DEFAULT 0",
                    "error_category": "TEXT",
                    "provider_metadata": "TEXT",
                    "embedding_space": "TEXT",
                    "timing_ms": "INTEGER",
                },
            )
        elif migration_id == "D0008_003_provider_ledger_indexes_and_triggers":
            self._apply_provider_ledger_constraints(conn)
        elif migration_id == "D0008_004_timed_transcript_units":
            conn.execute(
                "CREATE TABLE IF NOT EXISTS transcript_timed_units (asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE, chunk_index INTEGER NOT NULL, unit_index INTEGER NOT NULL, text TEXT NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL, confidence REAL, language TEXT, token_kind TEXT NOT NULL, PRIMARY KEY(asset_id, chunk_index, unit_index), FOREIGN KEY(asset_id, chunk_index) REFERENCES transcript_chunks(asset_id, chunk_index) ON DELETE CASCADE)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_timed_units_asset_chunk_unit ON transcript_timed_units(asset_id, chunk_index, unit_index)"
            )

    def _verify_migration(self, conn: sqlite3.Connection, migration_id: str) -> None:
        table_requirements: dict[str, tuple[_ColumnSpec, ...]] = {
            "H0001_assets": (
                _ColumnSpec("id", "TEXT", primary_key_order=1),
                _ColumnSpec("filename", "TEXT", True),
                _ColumnSpec("media_type", "TEXT", True),
                _ColumnSpec("original_path", "TEXT", True),
                _ColumnSpec("status", "TEXT", True),
                _ColumnSpec("created_at", "INTEGER", True),
                _ColumnSpec("updated_at", "INTEGER", True),
            ),
            "H0002_folders": (
                _ColumnSpec("id", "TEXT", primary_key_order=1),
                _ColumnSpec("name", "TEXT", True),
                _ColumnSpec("parent_id", "TEXT"),
                _ColumnSpec("created_at", "INTEGER", True),
                _ColumnSpec("updated_at", "INTEGER", True),
            ),
            "H0003_speakers": (
                _ColumnSpec("id", "TEXT", primary_key_order=1),
                _ColumnSpec("display_name", "TEXT", True),
                _ColumnSpec("centroid", "TEXT", True),
                _ColumnSpec("sample_count", "INTEGER", True),
                _ColumnSpec("created_at", "INTEGER", True),
                _ColumnSpec("updated_at", "INTEGER", True),
            ),
            "H0004_jobs_and_claim_columns": (
                _ColumnSpec("id", "TEXT", primary_key_order=1),
                _ColumnSpec("asset_id", "TEXT", True),
                _ColumnSpec("status", "TEXT", True),
                _ColumnSpec("stage", "TEXT"),
                _ColumnSpec("run_attempt", "INTEGER", True, "0"),
                _ColumnSpec("claim_owner", "TEXT"),
                _ColumnSpec("claim_expires_at", "INTEGER"),
                _ColumnSpec("created_at", "INTEGER", True),
                _ColumnSpec("started_at", "INTEGER"),
                _ColumnSpec("finished_at", "INTEGER"),
            ),
            "H0005_job_events_run_attempt": (
                _ColumnSpec("id", "INTEGER", primary_key_order=1),
                _ColumnSpec("job_id", "TEXT", True),
                _ColumnSpec("asset_id", "TEXT", True),
                _ColumnSpec("level", "TEXT", True),
                _ColumnSpec("stage", "TEXT"),
                _ColumnSpec("message", "TEXT", True),
                _ColumnSpec("payload", "TEXT"),
                _ColumnSpec("run_attempt", "INTEGER", default="0"),
                _ColumnSpec("created_at", "INTEGER", True),
            ),
            "H0008_upload_sessions": (
                _ColumnSpec("id", "TEXT", primary_key_order=1),
                _ColumnSpec("filename", "TEXT", True),
                _ColumnSpec("total_size", "INTEGER", True),
                _ColumnSpec("offset", "INTEGER", True, "0"),
                _ColumnSpec("temp_path", "TEXT", True),
                _ColumnSpec("created_at", "INTEGER", True),
                _ColumnSpec("updated_at", "INTEGER", True),
            ),
        }
        table_by_migration = {
            "H0001_assets": "assets",
            "H0002_folders": "folders",
            "H0003_speakers": "speakers",
            "H0004_jobs_and_claim_columns": "jobs",
            "H0005_job_events_run_attempt": "job_events",
            "H0008_upload_sessions": "upload_sessions",
        }
        if migration_id in table_by_migration:
            self._require_table_shape(
                conn,
                table_by_migration[migration_id],
                table_requirements[migration_id],
                migration_id,
            )
        if migration_id == "H0002_folders":
            self._require_foreign_keys(
                conn,
                "folders",
                (_ForeignKeySpec("folders", "parent_id", "id", "RESTRICT"),),
                migration_id,
            )
        elif migration_id == "H0004_jobs_and_claim_columns":
            self._require_foreign_keys(
                conn,
                "jobs",
                (_ForeignKeySpec("assets", "asset_id", "id", "CASCADE"),),
                migration_id,
            )
        elif migration_id == "H0005_job_events_run_attempt":
            self._require_foreign_keys(
                conn,
                "job_events",
                (
                    _ForeignKeySpec("jobs", "job_id", "id", "CASCADE"),
                    _ForeignKeySpec("assets", "asset_id", "id", "CASCADE"),
                ),
                migration_id,
            )
        elif migration_id == "H0006_transcript_chunks":
            self._require_table_shape(
                conn,
                "transcript_chunks",
                (
                    _ColumnSpec("asset_id", "TEXT", True, primary_key_order=1),
                    _ColumnSpec("chunk_index", "INTEGER", True, primary_key_order=2),
                    _ColumnSpec("start", "REAL", True),
                    _ColumnSpec("end", "REAL", True),
                    _ColumnSpec("chunk_start", "REAL", True),
                    _ColumnSpec("chunk_end", "REAL", True),
                    _ColumnSpec("speaker", "TEXT", True),
                    _ColumnSpec("speaker_id", "TEXT"),
                    _ColumnSpec("speaker_name", "TEXT"),
                    _ColumnSpec("speaker_similarity", "REAL"),
                    _ColumnSpec("text", "TEXT", True),
                    _ColumnSpec("status", "TEXT", True),
                    _ColumnSpec("attempts", "INTEGER", True),
                    _ColumnSpec("error", "TEXT"),
                    _ColumnSpec("updated_at", "INTEGER", True),
                ),
                migration_id,
            )
            self._require_table_shape(
                conn,
                "asset_visual_events",
                (
                    _ColumnSpec("asset_id", "TEXT", True, primary_key_order=1),
                    _ColumnSpec("event_index", "INTEGER", True, primary_key_order=2),
                    _ColumnSpec("timestamp", "REAL", True),
                    _ColumnSpec("score", "REAL", True),
                    _ColumnSpec("kind", "TEXT", True),
                    _ColumnSpec("created_at", "INTEGER", True),
                ),
                migration_id,
            )
            self._require_foreign_keys(
                conn,
                "transcript_chunks",
                (_ForeignKeySpec("assets", "asset_id", "id", "CASCADE"),),
                migration_id,
            )
            self._require_foreign_keys(
                conn,
                "asset_visual_events",
                (_ForeignKeySpec("assets", "asset_id", "id", "CASCADE"),),
                migration_id,
            )
        elif migration_id == "H0007_asset_metadata_columns":
            self._require_table_shape(
                conn,
                "assets",
                tuple(
                    _ColumnSpec(name, column_type)
                    for name, column_type in (
                        ("parent_folder_id", "TEXT"),
                        ("title", "TEXT"),
                        ("recorded_at", "INTEGER"),
                        ("wav_path", "TEXT"),
                        ("duration", "REAL"),
                        ("error", "TEXT"),
                        ("diarization_stats", "TEXT"),
                        ("raw_segments", "TEXT"),
                        ("merged_segments", "TEXT"),
                        ("speaker_centroids", "TEXT"),
                        ("transcript_segments", "TEXT"),
                        ("exports", "TEXT"),
                        ("summary_status", "TEXT"),
                        ("summary_text", "TEXT"),
                        ("summary_error", "TEXT"),
                        ("summary_model", "TEXT"),
                        ("summary_updated_at", "INTEGER"),
                    )
                ),
                migration_id,
            )
            self._require_foreign_keys(
                conn,
                "assets",
                (_ForeignKeySpec("folders", "parent_folder_id", "id", "SET NULL"),),
                migration_id,
            )
            self._require_table_shape(
                conn,
                "jobs",
                (
                    _ColumnSpec("error", "TEXT"),
                    _ColumnSpec("progress_total_chunks", "INTEGER", default="0"),
                    _ColumnSpec("progress_done_chunks", "INTEGER", default="0"),
                    _ColumnSpec("progress_failed_chunks", "INTEGER", default="0"),
                    _ColumnSpec("next_retry_at", "INTEGER"),
                    _ColumnSpec("provider_work_generation", "INTEGER", True, "1"),
                ),
                migration_id,
            )
        elif migration_id == "H0008_upload_sessions":
            self._require_table_shape(
                conn,
                "asset_cleanup_tasks",
                (
                    _ColumnSpec("asset_id", "TEXT", primary_key_order=1),
                    _ColumnSpec("media_path", "TEXT", True),
                    _ColumnSpec("exports_path", "TEXT", True),
                    _ColumnSpec("created_at", "INTEGER", True),
                ),
                migration_id,
            )
        elif migration_id == "D0008_002_embedding_space_columns":
            for table, columns in {
                "assets": ("embedding_space",),
                "speakers": ("embedding_space",),
                "provider_work_items": ("completed_at",),
                "provider_invocations": (
                    "duplicate_recovery",
                    "error_category",
                    "provider_metadata",
                    "embedding_space",
                    "timing_ms",
                ),
            }.items():
                specs = tuple(
                    _ColumnSpec(name, "INTEGER", True, "0")
                    if name == "duplicate_recovery"
                    else _ColumnSpec(name, "INTEGER")
                    if name in {"completed_at", "timing_ms"}
                    else _ColumnSpec(name, "TEXT")
                    for name in columns
                )
                self._require_table_shape(conn, table, specs, migration_id)
        elif migration_id == "D0008_004_timed_transcript_units":
            self._require_table_shape(
                conn,
                "transcript_timed_units",
                (
                    _ColumnSpec("asset_id", "TEXT", True, primary_key_order=1),
                    _ColumnSpec("chunk_index", "INTEGER", True, primary_key_order=2),
                    _ColumnSpec("unit_index", "INTEGER", True, primary_key_order=3),
                    _ColumnSpec("text", "TEXT", True),
                    _ColumnSpec("start_ms", "INTEGER", True),
                    _ColumnSpec("end_ms", "INTEGER", True),
                    _ColumnSpec("confidence", "REAL"),
                    _ColumnSpec("language", "TEXT"),
                    _ColumnSpec("token_kind", "TEXT", True),
                ),
                migration_id,
            )
            self._require_foreign_keys(
                conn,
                "transcript_timed_units",
                (
                    _ForeignKeySpec("assets", "asset_id", "id", "CASCADE"),
                    _ForeignKeySpec("transcript_chunks", "asset_id", "asset_id", "CASCADE"),
                    _ForeignKeySpec("transcript_chunks", "chunk_index", "chunk_index", "CASCADE"),
                ),
                migration_id,
            )
        if migration_id == "H0009_historical_indexes":
            self._require_indexes(
                conn,
                {
                    "idx_assets_created_at": _IndexSpec("assets", ("created_at",)),
                    "idx_assets_parent_folder_id": _IndexSpec("assets", ("parent_folder_id",)),
                    "idx_assets_recorded_at": _IndexSpec("assets", ("recorded_at",)),
                    "idx_folders_parent_id": _IndexSpec("folders", ("parent_id",)),
                    "idx_jobs_status_created_at": _IndexSpec("jobs", ("status", "created_at")),
                    "idx_jobs_processing_claim": _IndexSpec("jobs", ("status", "claim_expires_at")),
                    "idx_job_events_asset_created_at": _IndexSpec(
                        "job_events", ("asset_id", "created_at")
                    ),
                    "idx_transcript_chunks_asset_index": _IndexSpec(
                        "transcript_chunks", ("asset_id", "chunk_index")
                    ),
                    "idx_visual_events_asset_index": _IndexSpec(
                        "asset_visual_events", ("asset_id", "event_index")
                    ),
                },
                migration_id,
            )
        elif migration_id == "D0008_001_provider_ledger_tables":
            self._validate_provider_ledger_adoption_preconditions(conn)
            self._validate_recovery_reservation_schema(conn, migration_id)
        elif migration_id == "D0008_003_provider_ledger_indexes_and_triggers":
            self._validate_provider_ledger_adoption_preconditions(conn)
            self._require_indexes(
                conn,
                {
                    "idx_provider_active_invocations": _IndexSpec(
                        "provider_invocations", ("state", "work_item_id", "invocation_attempt")
                    ),
                    "idx_provider_work_items_recovery": _IndexSpec(
                        "provider_work_items", ("state", "work_item_id")
                    ),
                    "idx_active_job_recovery_reservation": _IndexSpec(
                        "job_recovery_reservations", ("job_id",), True, "state = 'active'"
                    ),
                },
                migration_id,
            )
            self._require_triggers(
                conn,
                {
                    "provider_work_items_immutable_update",
                    "provider_invocations_immutable_update",
                    "provider_invocation_transitions_append_only_update",
                    "provider_invocation_transitions_append_only_delete",
                },
                migration_id,
            )

    @staticmethod
    def _require_table_shape(
        conn: sqlite3.Connection, table: str, specs: tuple[_ColumnSpec, ...], migration_id: str
    ) -> None:
        columns = {str(row["name"]): row for row in conn.execute(f"PRAGMA table_info({table})")}
        for spec in specs:
            row = columns.get(spec.name)
            if (
                row is None
                or str(row["type"]).upper() != spec.column_type
                or bool(row["notnull"]) != spec.not_null
                or (None if row["dflt_value"] is None else str(row["dflt_value"]).strip("()'\""))
                != spec.default
                or int(row["pk"]) != spec.primary_key_order
            ):
                raise MigrationStateError(
                    f"{migration_id} has an incompatible {table}.{spec.name} definition"
                )

    @staticmethod
    def _require_foreign_keys(
        conn: sqlite3.Connection, table: str, specs: tuple[_ForeignKeySpec, ...], migration_id: str
    ) -> None:
        actual = {
            (str(row["table"]), str(row["from"]), str(row["to"]), str(row["on_delete"]).upper())
            for row in conn.execute(f"PRAGMA foreign_key_list({table})")
        }
        expected = {
            (spec.table, spec.from_column, spec.to_column, spec.on_delete) for spec in specs
        }
        if not expected <= actual:
            raise MigrationStateError(f"{migration_id} has incompatible {table} foreign keys")

    @staticmethod
    def _require_indexes(
        conn: sqlite3.Connection, specs: dict[str, _IndexSpec], migration_id: str
    ) -> None:
        for name, spec in specs.items():
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?", (name,)
            ).fetchone()
            index = next(
                (
                    item
                    for item in conn.execute(f"PRAGMA index_list({spec.table})")
                    if str(item["name"]) == name
                ),
                None,
            )
            columns = (
                ()
                if index is None
                else tuple(str(item["name"]) for item in conn.execute(f"PRAGMA index_info({name})"))
            )
            sql = " ".join(str(row["sql"] or "").lower().split()) if row else ""
            predicate = None if " where " not in sql else sql.split(" where ", 1)[1]
            if (
                index is None
                or columns != spec.columns
                or bool(index["unique"]) != spec.unique
                or predicate != spec.predicate
                or (name == "idx_assets_recorded_at" and "on assets(recorded_at desc)" not in sql)
            ):
                raise MigrationStateError(f"{migration_id} has an incompatible {name} index")

    @staticmethod
    def _require_triggers(conn: sqlite3.Connection, names: set[str], migration_id: str) -> None:
        actual = {
            str(row["name"])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
        }
        if not names <= actual:
            raise MigrationStateError(f"{migration_id} has missing required triggers")

    def _apply_provider_ledger_tables(self, conn: sqlite3.Connection) -> None:
        statements = (
            "CREATE TABLE IF NOT EXISTS provider_work_items (work_item_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE, asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE, role TEXT NOT NULL, provider_id TEXT NOT NULL, image_digest TEXT NOT NULL, chunk_key TEXT NOT NULL, work_generation INTEGER NOT NULL, idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL, original_run_attempt INTEGER NOT NULL, state TEXT NOT NULL CHECK(state IN ('prepared', 'sent', 'accepted', 'completed', 'cancelled', 'failed')), current_invocation_attempt INTEGER NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, UNIQUE(job_id, asset_id, role, provider_id, image_digest, chunk_key, work_generation))",
            "CREATE TABLE IF NOT EXISTS provider_invocations (work_item_id TEXT NOT NULL REFERENCES provider_work_items(work_item_id) ON DELETE CASCADE, invocation_attempt INTEGER NOT NULL, run_attempt INTEGER NOT NULL, correlation_id TEXT NOT NULL, idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('prepared', 'sent', 'accepted', 'completed', 'cancelled', 'failed')), prepared_at INTEGER NOT NULL, sent_at INTEGER, accepted_at INTEGER, completed_at INTEGER, cancelled_at INTEGER, failed_at INTEGER, cancellation_http_status INTEGER, PRIMARY KEY(work_item_id, invocation_attempt))",
            "CREATE TABLE IF NOT EXISTS provider_invocation_transitions (work_item_id TEXT NOT NULL, invocation_attempt INTEGER NOT NULL, sequence INTEGER NOT NULL, from_state TEXT, to_state TEXT NOT NULL CHECK(to_state IN ('prepared', 'sent', 'accepted', 'completed', 'cancelled', 'failed')), claimant_run_attempt INTEGER NOT NULL, cancellation_http_status INTEGER, created_at INTEGER NOT NULL, PRIMARY KEY(work_item_id, invocation_attempt, sequence))",
            "CREATE TABLE IF NOT EXISTS job_recovery_reservations (reservation_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE, token_sha256 TEXT NOT NULL UNIQUE CHECK(length(token_sha256) = 64), prior_run_attempt INTEGER NOT NULL CHECK(prior_run_attempt >= 0), stage TEXT NOT NULL, active_set_fingerprint TEXT NOT NULL CHECK(length(active_set_fingerprint) = 64), expires_at INTEGER NOT NULL, state TEXT NOT NULL CHECK(state IN ('active', 'completed', 'abandoned')), created_at INTEGER NOT NULL, finalized_at INTEGER)",
            "CREATE TABLE IF NOT EXISTS job_recovery_reservation_entries (reservation_id TEXT NOT NULL REFERENCES job_recovery_reservations(reservation_id) ON DELETE CASCADE, ordinal INTEGER NOT NULL, work_item_id TEXT NOT NULL, invocation_attempt INTEGER NOT NULL, expected_state TEXT NOT NULL CHECK(expected_state IN ('prepared', 'sent', 'accepted')), prior_run_attempt INTEGER NOT NULL, PRIMARY KEY(reservation_id, ordinal), UNIQUE(reservation_id, work_item_id, invocation_attempt))",
        )
        for statement in statements:
            conn.execute(statement)

    def _apply_provider_ledger_constraints(self, conn: sqlite3.Connection) -> None:
        self._validate_provider_ledger_schema(conn)
        statements = (
            "CREATE INDEX IF NOT EXISTS idx_provider_active_invocations ON provider_invocations(state, work_item_id, invocation_attempt)",
            "CREATE INDEX IF NOT EXISTS idx_provider_work_items_recovery ON provider_work_items(state, work_item_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_active_job_recovery_reservation ON job_recovery_reservations(job_id) WHERE state = 'active'",
            "CREATE TRIGGER IF NOT EXISTS provider_work_items_immutable_update BEFORE UPDATE ON provider_work_items WHEN OLD.work_item_id IS NOT NEW.work_item_id OR OLD.job_id IS NOT NEW.job_id OR OLD.asset_id IS NOT NEW.asset_id OR OLD.role IS NOT NEW.role OR OLD.provider_id IS NOT NEW.provider_id OR OLD.image_digest IS NOT NEW.image_digest OR OLD.chunk_key IS NOT NEW.chunk_key OR OLD.work_generation IS NOT NEW.work_generation OR OLD.idempotency_key IS NOT NEW.idempotency_key OR OLD.request_hash IS NOT NEW.request_hash OR OLD.original_run_attempt IS NOT NEW.original_run_attempt BEGIN SELECT RAISE(ABORT, 'immutable provider work item'); END",
            "CREATE TRIGGER IF NOT EXISTS provider_invocations_immutable_update BEFORE UPDATE ON provider_invocations WHEN OLD.work_item_id IS NOT NEW.work_item_id OR OLD.invocation_attempt IS NOT NEW.invocation_attempt OR OLD.run_attempt IS NOT NEW.run_attempt OR OLD.correlation_id IS NOT NEW.correlation_id OR OLD.idempotency_key IS NOT NEW.idempotency_key OR OLD.request_hash IS NOT NEW.request_hash OR OLD.duplicate_recovery IS NOT NEW.duplicate_recovery OR OLD.prepared_at IS NOT NEW.prepared_at BEGIN SELECT RAISE(ABORT, 'immutable provider invocation'); END",
            "CREATE TRIGGER IF NOT EXISTS provider_invocation_transitions_append_only_update BEFORE UPDATE ON provider_invocation_transitions BEGIN SELECT RAISE(ABORT, 'provider invocation transitions are append-only'); END",
            "CREATE TRIGGER IF NOT EXISTS provider_invocation_transitions_append_only_delete BEFORE DELETE ON provider_invocation_transitions BEGIN SELECT RAISE(ABORT, 'provider invocation transitions are append-only'); END",
        )
        for statement in statements:
            conn.execute(statement)

    @staticmethod
    def _add_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
        existing = {
            str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    @staticmethod
    def _validate_provider_ledger_schema(
        conn: sqlite3.Connection, *, include_d0008_additions: bool = True
    ) -> None:
        table_specs = {
            "provider_work_items": (
                _ColumnSpec("work_item_id", "TEXT", primary_key_order=1),
                _ColumnSpec("job_id", "TEXT", True),
                _ColumnSpec("asset_id", "TEXT", True),
                _ColumnSpec("role", "TEXT", True),
                _ColumnSpec("provider_id", "TEXT", True),
                _ColumnSpec("image_digest", "TEXT", True),
                _ColumnSpec("chunk_key", "TEXT", True),
                _ColumnSpec("work_generation", "INTEGER", True),
                _ColumnSpec("idempotency_key", "TEXT", True),
                _ColumnSpec("request_hash", "TEXT", True),
                _ColumnSpec("original_run_attempt", "INTEGER", True),
                _ColumnSpec("state", "TEXT", True),
                _ColumnSpec("current_invocation_attempt", "INTEGER", True),
                _ColumnSpec("created_at", "INTEGER", True),
                _ColumnSpec("updated_at", "INTEGER", True),
            ),
            "provider_invocations": (
                _ColumnSpec("work_item_id", "TEXT", True, primary_key_order=1),
                _ColumnSpec("invocation_attempt", "INTEGER", True, primary_key_order=2),
                _ColumnSpec("run_attempt", "INTEGER", True),
                _ColumnSpec("correlation_id", "TEXT", True),
                _ColumnSpec("idempotency_key", "TEXT", True),
                _ColumnSpec("request_hash", "TEXT", True),
                _ColumnSpec("state", "TEXT", True),
                _ColumnSpec("prepared_at", "INTEGER", True),
                _ColumnSpec("sent_at", "INTEGER"),
                _ColumnSpec("accepted_at", "INTEGER"),
                _ColumnSpec("completed_at", "INTEGER"),
                _ColumnSpec("cancelled_at", "INTEGER"),
                _ColumnSpec("failed_at", "INTEGER"),
                _ColumnSpec("cancellation_http_status", "INTEGER"),
            ),
            "provider_invocation_transitions": (
                _ColumnSpec("work_item_id", "TEXT", True, primary_key_order=1),
                _ColumnSpec("invocation_attempt", "INTEGER", True, primary_key_order=2),
                _ColumnSpec("sequence", "INTEGER", True, primary_key_order=3),
                _ColumnSpec("from_state", "TEXT"),
                _ColumnSpec("to_state", "TEXT", True),
                _ColumnSpec("claimant_run_attempt", "INTEGER", True),
                _ColumnSpec("cancellation_http_status", "INTEGER"),
                _ColumnSpec("created_at", "INTEGER", True),
            ),
        }
        if include_d0008_additions:
            table_specs["provider_work_items"] += (_ColumnSpec("completed_at", "INTEGER"),)
            table_specs["provider_invocations"] += (
                _ColumnSpec("duplicate_recovery", "INTEGER", True, "0"),
                _ColumnSpec("error_category", "TEXT"),
                _ColumnSpec("provider_metadata", "TEXT"),
                _ColumnSpec("embedding_space", "TEXT"),
                _ColumnSpec("timing_ms", "INTEGER"),
            )
        for table, specs in table_specs.items():
            SqliteDatabase._require_table_shape(conn, table, specs, "provider ledger")

    @staticmethod
    def _provider_table_names(conn: sqlite3.Connection) -> set[str]:
        return {
            str(row["name"])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN (?, ?, ?)",
                (
                    "provider_work_items",
                    "provider_invocations",
                    "provider_invocation_transitions",
                ),
            )
        }

    @classmethod
    def _validate_provider_ledger_adoption_preconditions(cls, conn: sqlite3.Connection) -> None:
        provider_tables = cls._provider_table_names(conn)
        if not provider_tables:
            return
        required_tables = {
            "provider_work_items",
            "provider_invocations",
            "provider_invocation_transitions",
        }
        if provider_tables != required_tables:
            raise MigrationStateError("provider ledger tables are partial and cannot be adopted")
        cls._validate_provider_ledger_schema(conn, include_d0008_additions=False)

        required_columns = {
            "provider_work_items": {
                "work_item_id",
                "job_id",
                "asset_id",
                "role",
                "provider_id",
                "image_digest",
                "chunk_key",
                "work_generation",
                "idempotency_key",
                "request_hash",
                "original_run_attempt",
                "state",
                "current_invocation_attempt",
                "created_at",
                "updated_at",
            },
            "provider_invocations": {
                "work_item_id",
                "invocation_attempt",
                "run_attempt",
                "correlation_id",
                "idempotency_key",
                "request_hash",
                "state",
                "prepared_at",
                "sent_at",
                "accepted_at",
                "completed_at",
                "cancelled_at",
                "failed_at",
                "cancellation_http_status",
            },
            "provider_invocation_transitions": {
                "work_item_id",
                "invocation_attempt",
                "sequence",
                "from_state",
                "to_state",
                "claimant_run_attempt",
                "cancellation_http_status",
                "created_at",
            },
        }
        required_column_types = {
            "provider_work_items": {
                "work_item_id": "TEXT",
                "job_id": "TEXT",
                "asset_id": "TEXT",
                "role": "TEXT",
                "provider_id": "TEXT",
                "image_digest": "TEXT",
                "chunk_key": "TEXT",
                "work_generation": "INTEGER",
                "idempotency_key": "TEXT",
                "request_hash": "TEXT",
                "original_run_attempt": "INTEGER",
                "state": "TEXT",
                "current_invocation_attempt": "INTEGER",
                "created_at": "INTEGER",
                "updated_at": "INTEGER",
            },
            "provider_invocations": {
                "work_item_id": "TEXT",
                "invocation_attempt": "INTEGER",
                "run_attempt": "INTEGER",
                "correlation_id": "TEXT",
                "idempotency_key": "TEXT",
                "request_hash": "TEXT",
                "state": "TEXT",
                "prepared_at": "INTEGER",
                "sent_at": "INTEGER",
                "accepted_at": "INTEGER",
                "completed_at": "INTEGER",
                "cancelled_at": "INTEGER",
                "failed_at": "INTEGER",
                "cancellation_http_status": "INTEGER",
            },
            "provider_invocation_transitions": {
                "work_item_id": "TEXT",
                "invocation_attempt": "INTEGER",
                "sequence": "INTEGER",
                "from_state": "TEXT",
                "to_state": "TEXT",
                "claimant_run_attempt": "INTEGER",
                "cancellation_http_status": "INTEGER",
                "created_at": "INTEGER",
            },
        }
        for table, required in required_columns.items():
            columns = {
                str(row["name"]): row
                for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            missing = required - columns.keys()
            if missing:
                raise MigrationStateError(
                    f"{table} is incompatible with the provider ledger: missing {', '.join(sorted(missing))}"
                )
            incompatible_types = sorted(
                name
                for name, expected_type in required_column_types[table].items()
                if str(columns[name]["type"]).upper() != expected_type
            )
            if incompatible_types:
                raise MigrationStateError(
                    f"{table} is incompatible with the provider ledger: invalid types "
                    f"{', '.join(incompatible_types)}"
                )
        primary_keys = {
            "provider_work_items": ("work_item_id",),
            "provider_invocations": ("work_item_id", "invocation_attempt"),
            "provider_invocation_transitions": ("work_item_id", "invocation_attempt", "sequence"),
        }
        for table, expected_key in primary_keys.items():
            actual_key = tuple(
                str(row["name"])
                for row in sorted(
                    (row for row in conn.execute(f"PRAGMA table_info({table})") if row["pk"]),
                    key=lambda row: int(row["pk"]),
                )
            )
            if actual_key != expected_key:
                raise MigrationStateError(f"{table} has an incompatible primary key")
        unique_work_item_identity = (
            "job_id",
            "asset_id",
            "role",
            "provider_id",
            "image_digest",
            "chunk_key",
            "work_generation",
        )
        unique_indexes = [
            str(row["name"])
            for row in conn.execute("PRAGMA index_list(provider_work_items)")
            if int(row["unique"])
        ]
        if unique_work_item_identity not in {
            tuple(
                str(row["name"])
                for row in conn.execute(f"PRAGMA index_info({index_name})").fetchall()
            )
            for index_name in unique_indexes
        }:
            raise MigrationStateError("provider work items have an incompatible identity key")
        foreign_keys = {
            (str(row["table"]), str(row["from"]), str(row["to"]), str(row["on_delete"]))
            for table in required_tables
            for row in conn.execute(f"PRAGMA foreign_key_list({table})")
        }
        expected_foreign_keys = {
            ("jobs", "job_id", "id", "CASCADE"),
            ("assets", "asset_id", "id", "CASCADE"),
            ("provider_work_items", "work_item_id", "work_item_id", "CASCADE"),
        }
        if not expected_foreign_keys <= foreign_keys:
            raise MigrationStateError("provider ledger has incompatible foreign keys")
        state_checks = {
            "provider_work_items": "state in ('prepared', 'sent', 'accepted', 'completed', 'cancelled', 'failed')",
            "provider_invocations": "state in ('prepared', 'sent', 'accepted', 'completed', 'cancelled', 'failed')",
            "provider_invocation_transitions": "to_state in ('prepared', 'sent', 'accepted', 'completed', 'cancelled', 'failed')",
        }
        for table, required_check in state_checks.items():
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
            ).fetchone()
            normalized_sql = " ".join(str(row["sql"] or "").lower().split()) if row else ""
            if required_check not in normalized_sql:
                raise MigrationStateError(f"{table} has incompatible state constraints")
        expected_indexes = {
            "idx_provider_active_invocations": (
                "provider_invocations",
                ("state", "work_item_id", "invocation_attempt"),
            ),
            "idx_provider_work_items_recovery": (
                "provider_work_items",
                ("state", "work_item_id"),
            ),
        }
        for name, (table, expected_columns) in expected_indexes.items():
            index = next(
                (
                    row
                    for row in conn.execute(f"PRAGMA index_list({table})")
                    if str(row["name"]) == name
                ),
                None,
            )
            if index is None:
                continue
            columns = tuple(
                str(row["name"]) for row in conn.execute(f"PRAGMA index_info({name})").fetchall()
            )
            if columns != expected_columns:
                raise MigrationStateError(f"{name} is incompatible with the provider ledger")
        expected_trigger_terms = {
            "provider_work_items_immutable_update": (
                "before update on provider_work_items",
                "old.work_item_id is not new.work_item_id",
                "old.original_run_attempt is not new.original_run_attempt",
                "immutable provider work item",
            ),
            "provider_invocations_immutable_update": (
                "before update on provider_invocations",
                "old.work_item_id is not new.work_item_id",
                "old.duplicate_recovery is not new.duplicate_recovery",
                "old.prepared_at is not new.prepared_at",
                "immutable provider invocation",
            ),
            "provider_invocation_transitions_append_only_update": (
                "before update on provider_invocation_transitions",
                "provider invocation transitions are append-only",
            ),
            "provider_invocation_transitions_append_only_delete": (
                "before delete on provider_invocation_transitions",
                "provider invocation transitions are append-only",
            ),
        }
        for name, required_terms in expected_trigger_terms.items():
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?", (name,)
            ).fetchone()
            if row is None:
                continue
            trigger_sql = str(row["sql"] or "").lower()
            if not all(term in trigger_sql for term in required_terms):
                raise MigrationStateError(f"{name} is incompatible with the provider ledger")
        cls._validate_provider_ledger_history(conn)

    @staticmethod
    def _validate_recovery_reservation_schema(conn: sqlite3.Connection, migration_id: str) -> None:
        SqliteDatabase._require_table_shape(
            conn,
            "job_recovery_reservations",
            (
                _ColumnSpec("reservation_id", "TEXT", primary_key_order=1),
                _ColumnSpec("job_id", "TEXT", True),
                _ColumnSpec("token_sha256", "TEXT", True),
                _ColumnSpec("prior_run_attempt", "INTEGER", True),
                _ColumnSpec("stage", "TEXT", True),
                _ColumnSpec("active_set_fingerprint", "TEXT", True),
                _ColumnSpec("expires_at", "INTEGER", True),
                _ColumnSpec("state", "TEXT", True),
                _ColumnSpec("created_at", "INTEGER", True),
                _ColumnSpec("finalized_at", "INTEGER"),
            ),
            migration_id,
        )
        entry_columns = (
            _ColumnSpec("reservation_id", "TEXT", True, primary_key_order=1),
            _ColumnSpec("ordinal", "INTEGER", True, primary_key_order=2),
            _ColumnSpec("work_item_id", "TEXT", True),
            _ColumnSpec("invocation_attempt", "INTEGER", True),
            _ColumnSpec("expected_state", "TEXT", True),
            _ColumnSpec("prior_run_attempt", "INTEGER", True),
        )
        SqliteDatabase._require_table_shape(
            conn, "job_recovery_reservation_entries", entry_columns, migration_id
        )
        SqliteDatabase._require_foreign_keys(
            conn,
            "job_recovery_reservations",
            (_ForeignKeySpec("jobs", "job_id", "id", "CASCADE"),),
            migration_id,
        )
        SqliteDatabase._require_foreign_keys(
            conn,
            "job_recovery_reservation_entries",
            (
                _ForeignKeySpec(
                    "job_recovery_reservations", "reservation_id", "reservation_id", "CASCADE"
                ),
            ),
            migration_id,
        )
        SqliteDatabase._require_unique_index(
            conn, "job_recovery_reservations", ("token_sha256",), migration_id
        )
        SqliteDatabase._require_unique_index(
            conn,
            "job_recovery_reservation_entries",
            ("reservation_id", "work_item_id", "invocation_attempt"),
            migration_id,
        )
        SqliteDatabase._require_sql_terms(
            conn,
            "table",
            "job_recovery_reservations",
            (
                "check(length(token_sha256) = 64)",
                "check(prior_run_attempt >= 0)",
                "check(length(active_set_fingerprint) = 64)",
                "check(state in ('active', 'completed', 'abandoned'))",
            ),
            migration_id,
        )
        SqliteDatabase._require_sql_terms(
            conn,
            "table",
            "job_recovery_reservation_entries",
            ("check(expected_state in ('prepared', 'sent', 'accepted'))",),
            migration_id,
        )

    @staticmethod
    def _require_unique_index(
        conn: sqlite3.Connection, table: str, columns: tuple[str, ...], migration_id: str
    ) -> None:
        found = any(
            int(index["unique"])
            and tuple(
                str(row["name"]) for row in conn.execute(f"PRAGMA index_info({index['name']})")
            )
            == columns
            for index in conn.execute(f"PRAGMA index_list({table})")
        )
        if not found:
            raise MigrationStateError(f"{migration_id} has an incompatible {table} unique key")

    @staticmethod
    def _require_sql_terms(
        conn: sqlite3.Connection,
        object_type: str,
        name: str,
        terms: tuple[str, ...],
        migration_id: str,
    ) -> None:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = ? AND name = ?", (object_type, name)
        ).fetchone()
        sql = "".join(str(row["sql"] or "").lower().split()) if row else ""
        if not all("".join(term.split()) in sql for term in terms):
            raise MigrationStateError(f"{migration_id} has incompatible {name} constraints")

    @staticmethod
    def _validate_provider_ledger_history(conn: sqlite3.Connection) -> None:
        invalid_invocation = conn.execute(
            """SELECT 1 FROM provider_invocations invocation
            LEFT JOIN provider_work_items work_item ON work_item.work_item_id = invocation.work_item_id
            WHERE work_item.work_item_id IS NULL OR invocation.state NOT IN (?, ?, ?, ?, ?, ?)
            LIMIT 1""",
            (*_ACTIVE_STATES, *_TERMINAL_STATES),
        ).fetchone()
        if invalid_invocation is not None:
            raise MigrationStateError("provider invocations contain an orphan or invalid state")
        invalid_work_item = conn.execute(
            """SELECT 1 FROM provider_work_items work_item
            LEFT JOIN provider_invocations invocation ON invocation.work_item_id = work_item.work_item_id
                AND invocation.invocation_attempt = work_item.current_invocation_attempt
            WHERE invocation.work_item_id IS NULL OR work_item.state NOT IN (?, ?, ?, ?, ?, ?)
            LIMIT 1""",
            (*_ACTIVE_STATES, *_TERMINAL_STATES),
        ).fetchone()
        if invalid_work_item is not None:
            raise MigrationStateError("provider work items contain an invalid current invocation")
        invocation_without_history = conn.execute(
            """SELECT 1 FROM provider_invocations invocation
            LEFT JOIN provider_invocation_transitions transition
                ON transition.work_item_id = invocation.work_item_id
                AND transition.invocation_attempt = invocation.invocation_attempt
            WHERE transition.work_item_id IS NULL LIMIT 1"""
        ).fetchone()
        if invocation_without_history is not None:
            raise MigrationStateError("provider invocations are missing transition history")
        transitions = conn.execute(
            """SELECT transition.work_item_id, transition.invocation_attempt, transition.sequence,
            transition.from_state, transition.to_state, invocation.state FROM provider_invocation_transitions transition
            LEFT JOIN provider_invocations invocation ON invocation.work_item_id = transition.work_item_id
                AND invocation.invocation_attempt = transition.invocation_attempt
            ORDER BY transition.work_item_id, transition.invocation_attempt, transition.sequence"""
        ).fetchall()
        previous: tuple[str, int] | None = None
        expected_sequence = 1
        final_state: str | None = None
        invocation_state: str | None = None
        for row in transitions:
            key = (str(row["work_item_id"]), int(row["invocation_attempt"]))
            if row["state"] is None or str(row["to_state"]) not in _LEGAL_TRANSITIONS:
                raise MigrationStateError("provider transitions contain an orphan or invalid state")
            if key != previous:
                if previous is not None and final_state != invocation_state:
                    raise MigrationStateError(
                        "provider transition history does not match invocation state"
                    )
                previous, expected_sequence = key, 1
            if int(row["sequence"]) != expected_sequence:
                raise MigrationStateError(
                    "provider transition history has a non-contiguous sequence"
                )
            from_state = row["from_state"]
            if expected_sequence == 1:
                if from_state is not None or str(row["to_state"]) != "prepared":
                    raise MigrationStateError(
                        "provider transition history has an invalid initial state"
                    )
            elif (
                from_state != final_state
                or str(row["to_state"]) not in _LEGAL_TRANSITIONS[final_state]
            ):
                raise MigrationStateError("provider transition history has an illegal transition")
            expected_sequence += 1
            final_state, invocation_state = str(row["to_state"]), str(row["state"])
        if previous is not None and final_state != invocation_state:
            raise MigrationStateError("provider transition history does not match invocation state")

    def _create_asset_in_connection(
        self, conn: sqlite3.Connection, asset: NewAsset, timestamp: int
    ) -> None:
        conn.execute(
            """INSERT INTO assets (id, filename, recorded_at, media_type, parent_folder_id, original_path, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)""",
            (
                asset.asset_id,
                asset.filename,
                self._recorded_at_from_filename(asset.filename),
                asset.media_type,
                asset.parent_folder_id,
                str(asset.original_path),
                timestamp,
                timestamp,
            ),
        )
        conn.execute(
            "INSERT INTO jobs (id, asset_id, status, created_at) VALUES (?, ?, 'queued', ?)",
            (asset.asset_id, asset.asset_id, timestamp),
        )

    def _upsert_cleanup_task(self, conn: sqlite3.Connection, command: AssetCleanup) -> None:
        conn.execute(
            """INSERT INTO asset_cleanup_tasks (asset_id, media_path, exports_path, created_at) VALUES (?, ?, ?, ?)
        ON CONFLICT(asset_id) DO UPDATE SET media_path = excluded.media_path, exports_path = excluded.exports_path""",
            (command.asset_id, str(command.media_path), str(command.exports_path), self._now()),
        )

    def _refresh_speaker_transcript_caches(
        self, conn: sqlite3.Connection, speaker_id: str, timestamp: int
    ) -> None:
        rows = conn.execute(
            "SELECT DISTINCT asset_id FROM transcript_chunks WHERE speaker_id = ?", (speaker_id,)
        ).fetchall()
        for row in rows:
            self._sync_transcript_cache(conn, str(row["asset_id"]), timestamp)

    @staticmethod
    def _add_event_in_connection(
        conn: sqlite3.Connection,
        asset_id: str,
        level: str,
        stage: str | None,
        message: str,
        payload: ErrorRecord | None,
        timestamp: int,
        run_attempt: int | None = None,
    ) -> None:
        job = conn.execute(
            "SELECT id, run_attempt FROM jobs WHERE asset_id = ?", (asset_id,)
        ).fetchone()
        if job is not None:
            conn.execute(
                """INSERT INTO job_events (job_id, asset_id, level, stage, message, payload, run_attempt, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job["id"],
                    asset_id,
                    level,
                    stage,
                    message,
                    json.dumps(SqliteDatabase._error_payload(payload))
                    if payload is not None
                    else None,
                    run_attempt if run_attempt is not None else job["run_attempt"],
                    timestamp,
                ),
            )

    def _upsert_transcript_chunk(
        self, conn: sqlite3.Connection, command: TranscriptChunkUpsert, timestamp: int
    ) -> None:
        segment = command.segment
        conn.execute(
            """INSERT INTO transcript_chunks (asset_id, chunk_index, start, end, chunk_start, chunk_end, speaker, speaker_id, speaker_name, speaker_similarity, text, status, attempts, error, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(asset_id, chunk_index) DO UPDATE SET
        start = excluded.start, end = excluded.end, chunk_start = excluded.chunk_start, chunk_end = excluded.chunk_end, speaker = excluded.speaker, speaker_id = excluded.speaker_id, speaker_name = excluded.speaker_name, speaker_similarity = excluded.speaker_similarity, text = excluded.text, status = excluded.status, attempts = excluded.attempts, error = excluded.error, updated_at = excluded.updated_at""",
            (
                command.asset_id,
                command.chunk_index,
                segment.start,
                segment.end,
                segment.chunk_start if segment.chunk_start is not None else segment.start,
                segment.chunk_end if segment.chunk_end is not None else segment.end,
                segment.speaker,
                segment.speaker_id,
                segment.speaker_name,
                segment.speaker_similarity,
                segment.text,
                command.status,
                command.attempts,
                json.dumps(self._error_payload(command.error))
                if command.error is not None
                else None,
                timestamp,
            ),
        )
        self._sync_transcript_cache(conn, command.asset_id, timestamp)

    def _sync_transcript_cache(
        self, conn: sqlite3.Connection, asset_id: str, timestamp: int
    ) -> None:
        conn.execute(
            "UPDATE assets SET transcript_segments = ?, updated_at = ? WHERE id = ?",
            (
                json.dumps(
                    [
                        self._transcript_segment_payload(item)
                        for item in self._list_transcript_chunks_in_connection(conn, asset_id)
                    ]
                ),
                timestamp,
                asset_id,
            ),
        )

    def _cached_transcript_segments(
        self, conn: sqlite3.Connection, asset_id: str
    ) -> tuple[TranscriptSegment, ...]:
        row = conn.execute(
            "SELECT transcript_segments FROM assets WHERE id = ?", (asset_id,)
        ).fetchone()
        if row is None or row["transcript_segments"] is None:
            return ()
        value = json.loads(row["transcript_segments"])
        return tuple(self._transcript_segments(value))

    def _list_transcript_chunks_in_connection(
        self, conn: sqlite3.Connection, asset_id: str
    ) -> list[TranscriptSegment]:
        chunks: list[TranscriptSegment] = []
        for row in conn.execute(
            "SELECT * FROM transcript_chunks WHERE asset_id = ? ORDER BY chunk_index ASC",
            (asset_id,),
        ).fetchall():
            record = TranscriptChunkRecord.model_validate(self._decoded_row(row, {"error": dict}))
            chunks.append(
                TranscriptSegment(
                    start=record.start,
                    end=record.end,
                    speaker=record.speaker,
                    text=record.text,
                    chunk_index=record.chunk_index,
                    chunk_start=record.chunk_start,
                    chunk_end=record.chunk_end,
                    attempts=record.attempts,
                    speaker_id=record.speaker_id,
                    speaker_name=record.speaker_name,
                    speaker_similarity=record.speaker_similarity,
                )
            )
        return chunks

    @staticmethod
    def _replace_transcript_timed_units_in_connection(
        conn: sqlite3.Connection, command: ReplaceTranscriptTimedUnits
    ) -> None:
        conn.execute(
            "DELETE FROM transcript_timed_units WHERE asset_id = ? AND chunk_index = ?",
            (command.asset_id, command.chunk_index),
        )
        conn.executemany(
            """INSERT INTO transcript_timed_units (
            asset_id, chunk_index, unit_index, text, start_ms, end_ms, confidence, language, token_kind
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    command.asset_id,
                    command.chunk_index,
                    unit.unit_index,
                    unit.text,
                    unit.start_ms,
                    unit.end_ms,
                    unit.confidence,
                    unit.language,
                    unit.token_kind,
                )
                for unit in command.units
            ],
        )

    @staticmethod
    def _list_transcript_timed_units_in_connection(
        conn: sqlite3.Connection, asset_id: str
    ) -> list[PersistedTimedTranscriptUnit]:
        rows = conn.execute(
            """SELECT asset_id, chunk_index, unit_index, text, start_ms, end_ms, confidence, language,
            token_kind FROM transcript_timed_units WHERE asset_id = ? ORDER BY chunk_index, unit_index""",
            (asset_id,),
        ).fetchall()
        return [
            PersistedTimedTranscriptUnit(
                asset_id=str(row["asset_id"]),
                chunk_index=int(row["chunk_index"]),
                unit_index=int(row["unit_index"]),
                text=str(row["text"]),
                start_ms=int(row["start_ms"]),
                end_ms=int(row["end_ms"]),
                confidence=float(row["confidence"]) if row["confidence"] is not None else None,
                language=str(row["language"]) if row["language"] is not None else None,
                token_kind=str(row["token_kind"]),
            )
            for row in rows
        ]

    def _list_visual_events_in_connection(
        self, conn: sqlite3.Connection, asset_id: str
    ) -> list[PersistedVisualEvent]:
        events: list[PersistedVisualEvent] = []
        for row in conn.execute(
            "SELECT event_index, timestamp, score, kind, created_at FROM asset_visual_events WHERE asset_id = ? ORDER BY event_index ASC",
            (asset_id,),
        ).fetchall():
            event = VisualEventResponse.model_validate(dict(row))
            events.append(
                PersistedVisualEvent(
                    event.timestamp, event.score, event.kind, event.event_index, event.created_at
                )
            )
        return events

    def _asset_record(self, row: sqlite3.Row) -> AssetRecord:
        record = self._decoded_row(
            row,
            {
                "diarization_stats": dict,
                "raw_segments": list,
                "merged_segments": list,
                "speaker_centroids": dict,
                "transcript_segments": list,
                "exports": dict,
                "error": dict,
                "embedding_space": dict,
            },
        )
        embedding = record.get("embedding_space")
        return AssetRecord(
            id=str(record["id"]),
            filename=str(record["filename"]),
            media_type=str(record["media_type"]),
            original_path=str(record.get("original_path") or ""),
            status=str(record["status"]),
            created_at=int(record["created_at"]),
            updated_at=int(record["updated_at"]),
            title=record.get("title") if isinstance(record.get("title"), str) else None,
            recorded_at=record.get("recorded_at")
            if isinstance(record.get("recorded_at"), int)
            else None,
            wav_path=record.get("wav_path") if isinstance(record.get("wav_path"), str) else None,
            duration=float(record["duration"]) if record.get("duration") is not None else None,
            error=self._decoded_error(record.get("error")),
            diarization_stats=(
                record.get("diarization_stats")
                if isinstance(record.get("diarization_stats"), dict)
                else None
            ),
            exports=self._exports(record.get("exports")),
            raw_segments=tuple(self._speaker_segments(record.get("raw_segments"))),
            merged_segments=tuple(self._speaker_segments(record.get("merged_segments"))),
            transcript_segments=tuple(self._transcript_segments(record.get("transcript_segments"))),
            speaker_centroids=record.get("speaker_centroids") or {},
            embedding_space=self._embedding_space(embedding) if embedding is not None else None,
            summary_status=record.get("summary_status")
            if isinstance(record.get("summary_status"), str)
            else None,
            summary_text=record.get("summary_text")
            if isinstance(record.get("summary_text"), str)
            else None,
            summary_error=record.get("summary_error")
            if isinstance(record.get("summary_error"), str)
            else None,
            summary_model=record.get("summary_model")
            if isinstance(record.get("summary_model"), str)
            else None,
            summary_updated_at=record.get("summary_updated_at")
            if isinstance(record.get("summary_updated_at"), int)
            else None,
        )

    @staticmethod
    def _job_record(row: sqlite3.Row) -> JobRecord:
        error = SqliteDatabase._decoded_error(
            json.loads(row["error"]) if row["error"] is not None else None
        )
        return JobRecord(
            str(row["id"]),
            str(row["asset_id"]),
            str(row["status"]),
            int(row["created_at"]),
            row["stage"],
            error,
            row["started_at"],
            row["finished_at"],
            int(row["progress_total_chunks"] or 0),
            int(row["progress_done_chunks"] or 0),
            int(row["progress_failed_chunks"] or 0),
            row["next_retry_at"],
            int(row["run_attempt"] or 0),
            row["claim_owner"],
            row["claim_expires_at"],
        )

    def _list_events(
        self, asset_id: str, *, limit: int, run_attempt: int | None = None
    ) -> list[JobEventRecord]:
        with self._read_connection() as conn:
            return self._list_events_in_connection(
                conn, asset_id, limit=limit, run_attempt=run_attempt
            )

    def _list_events_in_connection(
        self,
        conn: sqlite3.Connection,
        asset_id: str,
        *,
        limit: int,
        run_attempt: int | None = None,
    ) -> list[JobEventRecord]:
        if limit < 1:
            raise ValueError("limit must be positive")
        query = """SELECT id, level, stage, message, payload, run_attempt, created_at FROM job_events
        WHERE asset_id = ?"""
        parameters: tuple[object, ...] = (asset_id,)
        if run_attempt is not None:
            query += " AND run_attempt = ?"
            parameters += (run_attempt,)
        query += " ORDER BY id DESC LIMIT ?"
        parameters += (limit,)
        rows = conn.execute(query, parameters).fetchall()
        events: list[JobEventRecord] = []
        for row in reversed(rows):
            payload = self._decoded_error(
                json.loads(row["payload"]) if row["payload"] is not None else None
            )
            events.append(
                JobEventRecord(
                    int(row["id"]),
                    str(row["level"]),
                    row["stage"],
                    str(row["message"]),
                    payload,
                    int(row["run_attempt"] or 0),
                    int(row["created_at"]),
                )
            )
        return events

    @staticmethod
    def _prepared_invocation(row: sqlite3.Row) -> PreparedProviderInvocation:
        return PreparedProviderInvocation(
            str(row["work_item_id"]),
            int(row["invocation_attempt"]),
            int(row["run_attempt"]),
            str(row["idempotency_key"]),
            str(row["request_hash"]),
            str(row["correlation_id"]),
            str(row["state"]),
        )

    @staticmethod
    def _provider_invocation_record(row: sqlite3.Row) -> ProviderInvocationRecord:
        metadata = json.loads(row["provider_metadata"]) if row["provider_metadata"] else None
        embedding = json.loads(row["embedding_space"]) if row["embedding_space"] else None
        if metadata is not None and not isinstance(metadata, dict):
            raise MigrationStateError("provider metadata has an invalid persisted shape")
        if embedding is not None and not isinstance(embedding, dict):
            raise MigrationStateError("provider embedding space has an invalid persisted shape")
        try:
            return ProviderInvocationRecord(
                str(row["work_item_id"]),
                int(row["invocation_attempt"]),
                int(row["run_attempt"]),
                str(row["correlation_id"]),
                str(row["idempotency_key"]),
                str(row["request_hash"]),
                bool(row["duplicate_recovery"]),
                str(row["state"]),
                int(row["prepared_at"]),
                row["sent_at"],
                row["accepted_at"],
                row["completed_at"],
                row["cancelled_at"],
                row["failed_at"],
                row["cancellation_http_status"],
                row["error_category"],
                ProviderMetadata(**metadata) if metadata is not None else None,
                EmbeddingSpaceV1.model_validate(embedding) if embedding is not None else None,
                row["timing_ms"],
            )
        except (TypeError, ValueError) as error:
            raise MigrationStateError(
                "provider invocation has an invalid persisted shape"
            ) from error

    def _speaker_record(self, row: sqlite3.Row) -> SpeakerRecord:
        record = self._decoded_row(row, {"centroid": list, "embedding_space": dict})
        centroid = record.get("centroid")
        if not isinstance(centroid, list) or any(
            isinstance(value, bool) or not isinstance(value, int | float) for value in centroid
        ):
            raise ValueError("speaker record has an invalid centroid")
        return SpeakerRecord(
            id=str(record["id"]),
            display_name=str(record["display_name"]),
            centroid=tuple(float(value) for value in centroid),
            sample_count=int(record["sample_count"]),
            created_at=int(record["created_at"]),
            updated_at=int(record["updated_at"]),
            embedding_space=(
                self._embedding_space(record["embedding_space"])
                if record.get("embedding_space") is not None
                else None
            ),
        )

    def _known_speaker(self, row: sqlite3.Row) -> SpeakerRecord:
        return self._speaker_record(row)

    @staticmethod
    def _embedding_space(value: object) -> EmbeddingSpaceV1:
        if isinstance(value, EmbeddingSpaceV1):
            return value
        if not isinstance(value, dict):
            raise EmbeddingSpaceConflictError("Speaker embedding space is incompatible")
        try:
            return EmbeddingSpaceV1.model_validate(value)
        except ValueError as error:
            raise EmbeddingSpaceConflictError("Speaker embedding space is incompatible") from error

    def _folder(self, row: sqlite3.Row) -> FolderResponse:
        try:
            return FolderResponse.model_validate(dict(row))
        except ValueError as error:
            raise FolderDataIntegrityError("Folder record is invalid") from error

    def _folder_asset(self, row: sqlite3.Row) -> FolderAssetSummary:
        try:
            return FolderAssetSummary.model_validate(self._decoded_row(row, {"error": dict}))
        except ValueError as error:
            raise FolderDataIntegrityError("Folder asset record is invalid") from error

    def _required_folder(
        self, conn: sqlite3.Connection, folder_id: str | None
    ) -> FolderResponse | None:
        if folder_id is None:
            return None
        row = conn.execute(
            "SELECT id, name, parent_id, created_at, updated_at FROM folders WHERE id = ?",
            (folder_id,),
        ).fetchone()
        if row is None:
            raise FolderNotFoundError(folder_id)
        return self._folder(row)

    @staticmethod
    def _normalize_folder_name(name: str) -> str:
        normalized = name.strip()
        if not normalized:
            raise ValueError("Folder name is required")
        if normalized in {".", ".."} or "/" in normalized or "\\" in normalized:
            raise ValueError("Folder name cannot contain a path separator")
        if "\x00" in normalized:
            raise ValueError("Folder name cannot contain a null byte")
        return normalized

    @staticmethod
    def _folder_is_descendant(conn: sqlite3.Connection, folder_id: str, candidate_id: str) -> bool:
        return (
            conn.execute(
                """WITH RECURSIVE descendants(id) AS (SELECT id FROM folders WHERE parent_id = ? UNION SELECT folders.id FROM folders JOIN descendants ON folders.parent_id = descendants.id) SELECT 1 FROM descendants WHERE id = ?""",
                (folder_id, candidate_id),
            ).fetchone()
            is not None
        )

    @staticmethod
    def _validate_folder_tree(roots: list[FolderTreeNodeResponse], expected_ids: set[str]) -> None:
        reachable: set[str] = set()
        pending = list(roots)
        while pending:
            node = pending.pop()
            if node.id in reachable:
                raise FolderDataIntegrityError("Folder appears more than once in the tree")
            reachable.add(node.id)
            pending.extend(node.children)
        if reachable != expected_ids:
            raise FolderDataIntegrityError("Folder tree contains a cycle or unreachable folder")

    @staticmethod
    def _decoded_row(row: sqlite3.Row, json_fields: dict[str, type[object]]) -> dict[str, object]:
        record = dict(row)
        for field, expected_type in json_fields.items():
            value = record.get(field)
            if value is None:
                continue
            decoded = json.loads(value)
            if not isinstance(decoded, expected_type):
                raise ValueError(f"{field} has an invalid persisted shape")
            record[field] = decoded
        return record

    @staticmethod
    def _error_payload(error: ErrorRecord) -> dict[str, str]:
        return {
            name: value
            for name, value in (
                ("category", error.category),
                ("message", error.message),
                ("cause", error.cause),
            )
            if value is not None
        }

    @staticmethod
    def _decoded_error(value: object) -> ErrorRecord | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise MigrationStateError("error record has an invalid persisted shape")
        category, message = value.get("category"), value.get("message")
        if not isinstance(category, str) or not isinstance(message, str):
            raise MigrationStateError("error record has an invalid persisted shape")
        cause = value.get("cause")
        return ErrorRecord(category, message, cause if isinstance(cause, str) else None)

    @staticmethod
    def _speaker_segment_payload(segment: SpeakerSegment) -> dict[str, object]:
        return {"start": segment.start, "end": segment.end, "speaker": segment.speaker}

    @staticmethod
    def _transcript_segment_payload(segment: TranscriptSegment) -> dict[str, object]:
        return {
            "start": segment.start,
            "end": segment.end,
            "speaker": segment.speaker,
            "text": segment.text,
            "chunk_index": segment.chunk_index,
            "chunk_start": segment.chunk_start,
            "chunk_end": segment.chunk_end,
            "attempts": segment.attempts,
            "speaker_id": segment.speaker_id,
            "speaker_name": segment.speaker_name,
            "speaker_similarity": segment.speaker_similarity,
        }

    @staticmethod
    def _export_payload(exports: ExportPaths) -> dict[str, str]:
        return {name: value for name, value in exports.__dict__.items() if value is not None}

    @staticmethod
    def _exports(value: object) -> ExportPaths:
        if not isinstance(value, dict):
            return ExportPaths()
        return ExportPaths(
            **{
                name: item
                for name, item in value.items()
                if name in ExportPaths.__dataclass_fields__ and isinstance(item, str)
            }
        )

    @staticmethod
    def _speaker_segments(value: object) -> list[SpeakerSegment]:
        if not isinstance(value, list):
            return []
        return [
            SpeakerSegment(float(item["start"]), float(item["end"]), str(item["speaker"]))
            for item in value
            if isinstance(item, dict) and {"start", "end", "speaker"} <= item.keys()
        ]

    @staticmethod
    def _transcript_segments(value: object) -> list[TranscriptSegment]:
        if not isinstance(value, list):
            return []
        return [
            TranscriptSegment(
                float(item["start"]),
                float(item["end"]),
                str(item["speaker"]),
                str(item["text"]),
                item.get("chunk_index"),
                item.get("chunk_start"),
                item.get("chunk_end"),
                item.get("attempts"),
                item.get("speaker_id"),
                item.get("speaker_name"),
                item.get("speaker_similarity"),
            )
            for item in value
            if isinstance(item, dict) and {"start", "end", "speaker", "text"} <= item.keys()
        ]

    @staticmethod
    def _recorded_at_from_filename(filename: str) -> int | None:
        stem = Path(filename).stem
        if not _RECORDED_AT_PATTERN.fullmatch(stem):
            return None
        try:
            return int(datetime.strptime(stem, "%Y-%m-%d_%H-%M-%S").replace(tzinfo=UTC).timestamp())
        except ValueError:
            return None

    @staticmethod
    def _validated_timing_ms(timing_ms: int | None) -> int | None:
        if timing_ms is None:
            return None
        if not isinstance(timing_ms, int) or isinstance(timing_ms, bool) or timing_ms < 0:
            raise ValueError("timing_ms must be a nonnegative integer")
        return timing_ms

    @staticmethod
    def _validated_transcript_chunk(
        command: CompleteTranscriptionProviderInvocation,
    ) -> TranscriptChunkUpsert:
        if not isinstance(command.attempts, int) or isinstance(command.attempts, bool):
            raise ValueError("attempts must be an integer")
        segment = command.segment
        start, end = segment.start, segment.end
        if (
            not isinstance(start, (int, float))
            or isinstance(start, bool)
            or not isinstance(end, (int, float))
            or isinstance(end, bool)
            or not isfinite(start)
            or not isfinite(end)
            or start > end
        ):
            raise ValueError("transcript segment bounds are invalid")
        if not isinstance(segment.speaker, str) or not isinstance(segment.text, str):
            raise ValueError("transcript speaker and text must be strings")
        return TranscriptChunkUpsert(
            command.asset_id, command.chunk_index, segment, command.attempts
        )

    @staticmethod
    def _validated_diarization_metadata(
        command: CompleteDiarizationProviderInvocation,
    ) -> tuple[str, float, str, str, str, str, str | None]:
        metadata = command.metadata
        if not isinstance(metadata, DiarizationMetadata):
            raise ValueError("diarization completion requires typed metadata")
        if metadata.asset_id != command.asset_id:
            raise ValueError("diarization metadata asset_id must match completion asset_id")
        if (
            not isinstance(metadata.duration, (int, float))
            or isinstance(metadata.duration, bool)
            or not isfinite(metadata.duration)
            or metadata.duration < 0
        ):
            raise ValueError("diarization duration must be nonnegative")
        for name, segments in (
            ("raw_segments", metadata.raw_segments),
            ("merged_segments", metadata.merged_segments),
        ):
            previous_end = 0.0
            for segment in segments:
                if not isinstance(segment, SpeakerSegment):
                    raise ValueError(f"{name} contains an invalid segment")
                start, end, speaker = segment.start, segment.end, segment.speaker
                if (
                    not isinstance(start, (int, float))
                    or isinstance(start, bool)
                    or not isinstance(end, (int, float))
                    or isinstance(end, bool)
                    or not isfinite(start)
                    or not isfinite(end)
                    or not isinstance(speaker, str)
                    or not speaker
                    or start < 0
                    or start < previous_end
                    or end < start
                    or end > metadata.duration
                ):
                    raise ValueError(f"{name} contains an invalid segment")
                previous_end = float(end)
        for speaker_id, centroid in metadata.speaker_centroids.entries:
            if (
                not isinstance(speaker_id, str)
                or not speaker_id
                or not isinstance(centroid, tuple)
                or not centroid
            ):
                raise ValueError("speaker_centroids contains an invalid speaker")
            if any(
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not isfinite(value)
                for value in centroid
            ):
                raise ValueError("speaker_centroids contains an invalid value")
        try:
            serialized = (
                json.dumps(metadata.diarization_stats.as_dict(), allow_nan=False),
                json.dumps(
                    [
                        SqliteDatabase._speaker_segment_payload(segment)
                        for segment in metadata.raw_segments
                    ],
                    allow_nan=False,
                ),
                json.dumps(
                    [
                        SqliteDatabase._speaker_segment_payload(segment)
                        for segment in metadata.merged_segments
                    ],
                    allow_nan=False,
                ),
                json.dumps(metadata.speaker_centroids.as_dict(), allow_nan=False),
            )
        except (TypeError, ValueError) as error:
            raise ValueError("diarization metadata is not valid JSON") from error
        embedding_space = (
            metadata.embedding_space.model_dump_json()
            if metadata.embedding_space is not None
            else None
        )
        return (str(metadata.wav_path), float(metadata.duration), *serialized, embedding_space)

    def prepare_provider_work_item(
        self, command: PrepareProviderWorkItem
    ) -> PreparedProviderInvocation:
        timestamp = self._now()
        with self._transaction() as conn:
            inserted = conn.execute(
                """INSERT INTO provider_work_items (
                work_item_id, job_id, asset_id, role, provider_id, image_digest, chunk_key,
                work_generation, idempotency_key, request_hash, original_run_attempt, state,
                current_invocation_attempt, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'prepared', 1, ?, ?)""",
                (
                    command.work_item_id,
                    command.job_id,
                    command.asset_id,
                    command.role,
                    command.provider_id,
                    command.image_digest,
                    command.chunk_key,
                    command.work_generation,
                    command.idempotency_key,
                    command.request_hash,
                    command.run_attempt,
                    timestamp,
                    timestamp,
                ),
            )
            if inserted.rowcount == 0:
                existing = self.find_provider_work_item(
                    FindProviderWorkItem(
                        command.job_id,
                        command.asset_id,
                        command.role,
                        command.provider_id,
                        command.image_digest,
                        command.chunk_key,
                        command.work_generation,
                    )
                )
                if existing is None:
                    raise MigrationStateError("provider work item identity is unreadable")
                if existing.request_hash != command.request_hash:
                    raise ValueError(
                        "provider work item request hash does not match immutable identity"
                    )
                return existing
            conn.execute(
                """INSERT INTO provider_invocations (
                work_item_id, invocation_attempt, run_attempt, correlation_id, idempotency_key,
                request_hash, duplicate_recovery, state, prepared_at) VALUES (?, 1, ?, ?, ?, ?, 0, 'prepared', ?)""",
                (
                    command.work_item_id,
                    command.run_attempt,
                    command.correlation_id,
                    command.idempotency_key,
                    command.request_hash,
                    timestamp,
                ),
            )
            conn.execute(
                """INSERT INTO provider_invocation_transitions (
                work_item_id, invocation_attempt, sequence, from_state, to_state,
                claimant_run_attempt, created_at) VALUES (?, 1, 1, NULL, 'prepared', ?, ?)""",
                (command.work_item_id, command.run_attempt, timestamp),
            )
            self._append_provider_audit_event(
                conn,
                command.work_item_id,
                1,
                "prepared",
                command.run_attempt,
                None,
                timestamp,
            )
        return PreparedProviderInvocation(
            command.work_item_id,
            1,
            command.run_attempt,
            command.idempotency_key,
            command.request_hash,
            command.correlation_id,
            "prepared",
        )

    def transition_provider_invocation(
        self, command: ProviderInvocationTransition
    ) -> TransitionResult:
        if command.to_state not in _LEGAL_TRANSITIONS[command.expected_state]:
            raise ValueError("illegal provider invocation transition")
        timestamp = self._now()
        state_column = f"{command.to_state}_at"
        metadata = (
            json.dumps(command.provider_metadata.as_dict(), sort_keys=True)
            if command.provider_metadata is not None
            else None
        )
        embedding_space = (
            command.embedding_space.model_dump_json()
            if command.embedding_space is not None
            else None
        )
        with self._transaction() as conn:
            updated = conn.execute(
                f"UPDATE provider_invocations SET state = ?, {state_column} = ?, "
                "cancellation_http_status = COALESCE(?, cancellation_http_status), "
                "error_category = COALESCE(?, error_category), "
                "provider_metadata = COALESCE(?, provider_metadata), "
                "embedding_space = COALESCE(?, embedding_space), timing_ms = COALESCE(?, timing_ms) "
                "WHERE work_item_id = ? AND invocation_attempt = ? AND state = ? AND run_attempt = ?",
                (
                    command.to_state,
                    timestamp,
                    command.cancellation_http_status,
                    command.error_category,
                    metadata,
                    embedding_space,
                    command.timing_ms,
                    command.work_item_id,
                    command.invocation_attempt,
                    command.expected_state,
                    command.claimant_run_attempt,
                ),
            )
            if updated.rowcount != 1:
                return TransitionResult(False)
            sequence = conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM provider_invocation_transitions "
                "WHERE work_item_id = ? AND invocation_attempt = ?",
                (command.work_item_id, command.invocation_attempt),
            ).fetchone()[0]
            conn.execute(
                """INSERT INTO provider_invocation_transitions (
                work_item_id, invocation_attempt, sequence, from_state, to_state,
                claimant_run_attempt, cancellation_http_status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    command.work_item_id,
                    command.invocation_attempt,
                    sequence,
                    command.expected_state,
                    command.to_state,
                    command.claimant_run_attempt,
                    command.cancellation_http_status,
                    timestamp,
                ),
            )
            conn.execute(
                "UPDATE provider_work_items SET state = ?, updated_at = ? WHERE work_item_id = ?",
                (command.to_state, timestamp, command.work_item_id),
            )
            self._append_provider_audit_event(
                conn,
                command.work_item_id,
                command.invocation_attempt,
                command.to_state,
                command.claimant_run_attempt,
                command.error_category,
                timestamp,
            )
        return TransitionResult(True)

    def find_provider_work_item(
        self, command: FindProviderWorkItem
    ) -> PreparedProviderInvocation | None:
        with self._read_connection() as conn:
            row = conn.execute(
                """SELECT invocation.work_item_id, invocation.invocation_attempt, invocation.run_attempt,
                invocation.idempotency_key, invocation.request_hash, invocation.correlation_id, invocation.state
                FROM provider_work_items AS work_item JOIN provider_invocations AS invocation
                ON invocation.work_item_id = work_item.work_item_id
                AND invocation.invocation_attempt = work_item.current_invocation_attempt
                WHERE work_item.job_id = ? AND work_item.asset_id = ? AND work_item.role = ?
                AND work_item.provider_id = ? AND work_item.image_digest = ? AND work_item.chunk_key = ?
                AND work_item.work_generation = ?""",
                (
                    command.job_id,
                    command.asset_id,
                    command.role,
                    command.provider_id,
                    command.image_digest,
                    command.chunk_key,
                    command.work_generation,
                ),
            ).fetchone()
        return None if row is None else self._prepared_invocation(row)

    def get_provider_invocation(
        self, work_item_id: str, invocation_attempt: int
    ) -> ProviderInvocationRecord | None:
        with self._read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM provider_invocations WHERE work_item_id = ? AND invocation_attempt = ?",
                (work_item_id, invocation_attempt),
            ).fetchone()
        return None if row is None else self._provider_invocation_record(row)

    def list_provider_invocation_transitions(
        self, work_item_id: str, invocation_attempt: int
    ) -> list[ProviderInvocationTransitionRecord]:
        with self._read_connection() as conn:
            rows = conn.execute(
                """SELECT sequence, from_state, to_state, claimant_run_attempt, cancellation_http_status,
                created_at FROM provider_invocation_transitions WHERE work_item_id = ?
                AND invocation_attempt = ? ORDER BY sequence""",
                (work_item_id, invocation_attempt),
            ).fetchall()
        return [
            ProviderInvocationTransitionRecord(
                int(row["sequence"]),
                row["from_state"],
                str(row["to_state"]),
                int(row["claimant_run_attempt"]),
                row["cancellation_http_status"],
                int(row["created_at"]),
            )
            for row in rows
        ]

    def complete_transcription_and_provider_invocation(
        self, command: CompleteTranscriptionProviderInvocation
    ) -> TransitionResult:
        transcript_chunk = self._validated_transcript_chunk(command)
        timed_units = ReplaceTranscriptTimedUnits(
            command.asset_id, command.chunk_index, command.timed_units
        )
        timing_ms = self._validated_timing_ms(command.timing_ms)
        timestamp = self._now()
        metadata = (
            json.dumps(command.provider_metadata.as_dict(), sort_keys=True)
            if command.provider_metadata is not None
            else None
        )
        with self._transaction() as conn:
            updated = conn.execute(
                """UPDATE provider_invocations SET state = 'completed', completed_at = ?,
                provider_metadata = COALESCE(?, provider_metadata), timing_ms = COALESCE(?, timing_ms)
                WHERE work_item_id = ? AND invocation_attempt = ? AND state = 'accepted' AND run_attempt = ?
                AND EXISTS (SELECT 1 FROM provider_work_items WHERE work_item_id = ?
                AND asset_id = ? AND role = 'transcription')""",
                (
                    timestamp,
                    metadata,
                    timing_ms,
                    command.work_item_id,
                    command.invocation_attempt,
                    command.claimant_run_attempt,
                    command.work_item_id,
                    command.asset_id,
                ),
            )
            if updated.rowcount != 1:
                return TransitionResult(False)
            self._append_transition_in_connection(
                conn,
                command.work_item_id,
                command.invocation_attempt,
                "accepted",
                "completed",
                command.claimant_run_attempt,
                timestamp,
            )
            conn.execute(
                """UPDATE provider_work_items SET state = 'completed', current_invocation_attempt = ?,
                completed_at = ?, updated_at = ? WHERE work_item_id = ?""",
                (command.invocation_attempt, timestamp, timestamp, command.work_item_id),
            )
            self._upsert_transcript_chunk(
                conn,
                transcript_chunk,
                timestamp,
            )
            self._replace_transcript_timed_units_in_connection(conn, timed_units)
            self._append_provider_audit_event(
                conn,
                command.work_item_id,
                command.invocation_attempt,
                "completed",
                command.claimant_run_attempt,
                None,
                timestamp,
            )
        return TransitionResult(True)

    def complete_diarization_and_provider_invocation(
        self, command: CompleteDiarizationProviderInvocation
    ) -> TransitionResult:
        diarization = self._validated_diarization_metadata(command)
        timing_ms = self._validated_timing_ms(command.timing_ms)
        provider_metadata = (
            json.dumps(command.provider_metadata.as_dict(), sort_keys=True)
            if command.provider_metadata is not None
            else None
        )
        timestamp = self._now()
        with self._transaction() as conn:
            updated = conn.execute(
                """UPDATE provider_invocations SET state = 'completed', completed_at = ?,
                provider_metadata = COALESCE(?, provider_metadata), embedding_space = COALESCE(?, embedding_space),
                timing_ms = COALESCE(?, timing_ms) WHERE work_item_id = ? AND invocation_attempt = ?
                AND state = 'accepted' AND run_attempt = ? AND EXISTS (
                SELECT 1 FROM provider_work_items WHERE work_item_id = ? AND asset_id = ?
                AND role = 'diarization')""",
                (
                    timestamp,
                    provider_metadata,
                    diarization[6],
                    timing_ms,
                    command.work_item_id,
                    command.invocation_attempt,
                    command.claimant_run_attempt,
                    command.work_item_id,
                    command.asset_id,
                ),
            )
            if updated.rowcount != 1:
                return TransitionResult(False)
            conn.execute(
                """UPDATE assets SET wav_path = ?, duration = ?, diarization_stats = ?, raw_segments = ?,
                merged_segments = ?, speaker_centroids = ?, embedding_space = ?, updated_at = ? WHERE id = ?""",
                (*diarization[:7], timestamp, command.asset_id),
            )
            self._append_transition_in_connection(
                conn,
                command.work_item_id,
                command.invocation_attempt,
                "accepted",
                "completed",
                command.claimant_run_attempt,
                timestamp,
            )
            conn.execute(
                """UPDATE provider_work_items SET state = 'completed', current_invocation_attempt = ?,
                completed_at = ?, updated_at = ? WHERE work_item_id = ?""",
                (command.invocation_attempt, timestamp, timestamp, command.work_item_id),
            )
            self._append_provider_audit_event(
                conn,
                command.work_item_id,
                command.invocation_attempt,
                "completed",
                command.claimant_run_attempt,
                None,
                timestamp,
            )
        return TransitionResult(True)

    def retry_provider_invocation(
        self, command: RetryProviderInvocation
    ) -> PreparedProviderInvocation | None:
        timestamp = self._now()
        with self._transaction() as conn:
            prior = conn.execute(
                """SELECT invocation_attempt, idempotency_key, request_hash FROM provider_invocations
                WHERE work_item_id = ? AND state = ? AND run_attempt = ?""",
                (command.work_item_id, command.expected_state, command.claimant_run_attempt),
            ).fetchone()
            if prior is None:
                return None
            if not self._transition_in_connection(
                conn,
                ProviderInvocationTransition(
                    command.work_item_id,
                    int(prior["invocation_attempt"]),
                    command.expected_state,
                    "failed",
                    command.claimant_run_attempt,
                    error_category=command.error_category,
                ),
                timestamp,
            ):
                return None
            next_attempt = int(prior["invocation_attempt"]) + 1
            self._insert_prepared_provider_invocation(
                conn,
                command.work_item_id,
                next_attempt,
                command.claimant_run_attempt,
                command.correlation_id,
                str(prior["idempotency_key"]),
                str(prior["request_hash"]),
                False,
                timestamp,
            )
            conn.execute(
                """UPDATE provider_work_items SET state = 'prepared', current_invocation_attempt = ?,
                updated_at = ? WHERE work_item_id = ?""",
                (next_attempt, timestamp, command.work_item_id),
            )
            self._append_provider_audit_event(
                conn,
                command.work_item_id,
                next_attempt,
                "prepared",
                command.claimant_run_attempt,
                None,
                timestamp,
            )
        return PreparedProviderInvocation(
            command.work_item_id,
            next_attempt,
            command.claimant_run_attempt,
            str(prior["idempotency_key"]),
            str(prior["request_hash"]),
            command.correlation_id,
            "prepared",
        )

    def claim_recoverable_jobs(self, command: ClaimRecoverableJobs) -> RecoveryClaimSet:
        if command.reservation_seconds < 1:
            raise ValueError("reservation_seconds must be positive")
        timestamp = command.now if command.now is not None else self._now()
        commands = []
        unsupported_phase: str | None = None
        with self._transaction() as conn:
            conn.execute(
                "UPDATE job_recovery_reservations SET state = 'abandoned', finalized_at = ? "
                "WHERE state = 'active' AND expires_at <= ?",
                (timestamp, timestamp),
            )
            jobs = conn.execute(
                """SELECT id, asset_id, run_attempt, stage FROM jobs
                WHERE status = 'processing' AND claim_expires_at <= ?
                ORDER BY created_at, id""",
                (timestamp,),
            ).fetchall()
            unsupported_jobs = [
                job
                for job in jobs
                if ("claimed" if job["stage"] == "starting" else str(job["stage"] or "claimed"))
                not in {"claimed", "transcoding", "diarizing", "transcribing speech"}
            ]
            if unsupported_jobs:
                unsupported_phase = str(unsupported_jobs[0]["stage"])
                conn.executemany(
                    """UPDATE job_recovery_reservations SET state = 'abandoned', finalized_at = ?
                    WHERE job_id = ? AND state = 'active'""",
                    [(timestamp, job["id"]) for job in unsupported_jobs],
                )
                jobs = ()
            for job in jobs:
                phase = "claimed" if job["stage"] == "starting" else str(job["stage"] or "claimed")
                if job["stage"] == "starting":
                    normalized = conn.execute(
                        """UPDATE jobs SET stage = 'claimed' WHERE id = ? AND status = 'processing'
                        AND run_attempt = ? AND stage = 'starting' AND claim_expires_at <= ?""",
                        (job["id"], job["run_attempt"], timestamp),
                    )
                    if normalized.rowcount != 1:
                        continue
                active = self._active_entries(conn, str(job["id"]), int(job["run_attempt"]))
                fingerprint = self._fingerprint(active)
                token = str(uuid4())
                reservation_id = str(uuid4())
                inserted = conn.execute(
                    """INSERT OR IGNORE INTO job_recovery_reservations (
                    reservation_id, job_id, token_sha256, prior_run_attempt, stage,
                    active_set_fingerprint, expires_at, state, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)""",
                    (
                        reservation_id,
                        job["id"],
                        self._token_hash(token),
                        job["run_attempt"],
                        phase,
                        fingerprint,
                        timestamp + command.reservation_seconds,
                        timestamp,
                    ),
                )
                if inserted.rowcount != 1:
                    continue
                for ordinal, entry in enumerate(active):
                    conn.execute(
                        """INSERT INTO job_recovery_reservation_entries (
                        reservation_id, ordinal, work_item_id, invocation_attempt, expected_state,
                        prior_run_attempt) VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            reservation_id,
                            ordinal,
                            entry.work_item_id,
                            entry.invocation_attempt,
                            entry.expected_state,
                            entry.prior_run_attempt,
                        ),
                    )
                if fingerprint != self._fingerprint(
                    self._active_entries(conn, str(job["id"]), int(job["run_attempt"]))
                ):
                    raise StaleClaimError("recovery active set changed during reservation")
                base = dict(
                    job_id=str(job["id"]),
                    asset_id=str(job["asset_id"]),
                    prior_run_attempt=int(job["run_attempt"]),
                    phase=phase,
                    token=token,
                )
                commands.append(
                    ProviderRecoveryCommand(entries=tuple(active), **base)
                    if active
                    else JobOnlyRecoveryCommand(**base)
                )
        if unsupported_phase is not None:
            raise ValueError(f"unsupported recovery stage: {unsupported_phase}")
        return RecoveryClaimSet(tuple(commands))

    def complete_provider_recovery(self, command: CompleteProviderRecovery) -> RecoveryCompletion:
        recovery = command.command
        timestamp = command.now if command.now is not None else self._now()
        with self._transaction() as conn:
            reservation = conn.execute(
                """SELECT reservation.reservation_id, reservation.active_set_fingerprint
                FROM job_recovery_reservations AS reservation
                JOIN jobs AS job ON job.id = reservation.job_id
                JOIN assets AS asset ON asset.id = job.asset_id
                WHERE reservation.job_id = ? AND job.asset_id = ?
                AND reservation.prior_run_attempt = ? AND reservation.token_sha256 = ?
                AND reservation.state = 'active' AND reservation.expires_at > ?
                AND reservation.stage = ? AND job.status = 'processing'
                AND job.run_attempt = ? AND job.stage = ? AND job.claim_expires_at <= ?
                AND asset.status = 'processing'
                """,
                (
                    recovery.job_id,
                    recovery.asset_id,
                    recovery.prior_run_attempt,
                    self._token_hash(recovery.token),
                    timestamp,
                    recovery.phase,
                    recovery.prior_run_attempt,
                    recovery.phase,
                    timestamp,
                ),
            ).fetchone()
            if reservation is None:
                raise StaleClaimError("recovery reservation is stale")
            if tuple(outcome.entry for outcome in command.outcomes) != recovery.entries:
                raise ValueError("recovery outcomes must match the complete ordered entry set")
            reserved_entries = conn.execute(
                """SELECT entry.work_item_id, entry.invocation_attempt, entry.prior_run_attempt,
                entry.expected_state, work_item.role, work_item.provider_id
                FROM job_recovery_reservation_entries AS entry
                JOIN provider_work_items AS work_item ON work_item.work_item_id = entry.work_item_id
                WHERE entry.reservation_id = ? ORDER BY entry.ordinal""",
                (reservation["reservation_id"],),
            ).fetchall()
            reserved_members = tuple(
                (
                    str(entry["work_item_id"]),
                    int(entry["invocation_attempt"]),
                    int(entry["prior_run_attempt"]),
                    str(entry["expected_state"]),
                    str(entry["role"]),
                    str(entry["provider_id"]),
                )
                for entry in reserved_entries
            )
            command_members = tuple(
                (
                    entry.work_item_id,
                    entry.invocation_attempt,
                    entry.prior_run_attempt,
                    entry.expected_state,
                    entry.role,
                    entry.provider_id,
                )
                for entry in recovery.entries
            )
            if reserved_members != command_members:
                raise StaleClaimError("recovery reservation entries are stale")
            live = self._active_entries(conn, recovery.job_id, recovery.prior_run_attempt)
            if (
                tuple(live) != recovery.entries
                or self._fingerprint(live) != reservation["active_set_fingerprint"]
            ):
                return RecoveryCompletion(False, True)
            if recovery.kind == "job_only" and command.outcomes:
                raise ValueError("job-only recovery has no outcomes")
            if recovery.kind == "provider_set" and any(
                outcome.kind == "cancelled" and outcome.http_status != 204
                for outcome in command.outcomes
            ):
                return RecoveryCompletion(False, True)
            for outcome in command.outcomes:
                if outcome.kind == "abandoned":
                    entry = outcome.entry
                    transition = ProviderInvocationTransition(
                        work_item_id=entry.work_item_id,
                        invocation_attempt=entry.invocation_attempt,
                        expected_state=entry.expected_state,
                        to_state="failed",
                        claimant_run_attempt=entry.prior_run_attempt,
                        error_category="process_lost",
                    )
                    if not self._transition_in_connection(conn, transition, timestamp):
                        return RecoveryCompletion(False, True)
                    continue
                entry = outcome.entry
                transition = ProviderInvocationTransition(
                    entry.work_item_id,
                    entry.invocation_attempt,
                    entry.expected_state,
                    "cancelled",
                    entry.prior_run_attempt,
                    outcome.http_status,
                )
                if not self._transition_in_connection(conn, transition, timestamp):
                    return RecoveryCompletion(False, True)
                prior = conn.execute(
                    """SELECT idempotency_key, request_hash FROM provider_invocations
                    WHERE work_item_id = ? AND invocation_attempt = ?""",
                    (entry.work_item_id, entry.invocation_attempt),
                ).fetchone()
                next_attempt = entry.invocation_attempt + 1
                conn.execute(
                    """INSERT INTO provider_invocations (
                    work_item_id, invocation_attempt, run_attempt, correlation_id, idempotency_key,
                    request_hash, state, prepared_at) VALUES (?, ?, ?, ?, ?, ?, 'prepared', ?)""",
                    (
                        entry.work_item_id,
                        next_attempt,
                        recovery.prior_run_attempt + 1,
                        str(uuid4()),
                        prior["idempotency_key"],
                        prior["request_hash"],
                        timestamp,
                    ),
                )
                conn.execute(
                    """INSERT INTO provider_invocation_transitions (
                    work_item_id, invocation_attempt, sequence, from_state, to_state,
                    claimant_run_attempt, created_at) VALUES (?, ?, 1, NULL, 'prepared', ?, ?)""",
                    (entry.work_item_id, next_attempt, recovery.prior_run_attempt + 1, timestamp),
                )
                conn.execute(
                    """UPDATE provider_work_items SET state = 'prepared',
                    current_invocation_attempt = ?, updated_at = ? WHERE work_item_id = ?""",
                    (next_attempt, timestamp, entry.work_item_id),
                )
            updated = conn.execute(
                """UPDATE jobs SET status = 'queued', stage = NULL, started_at = NULL,
                claim_owner = NULL, claim_expires_at = NULL WHERE id = ? AND status = 'processing'
                AND run_attempt = ? AND stage = ? AND claim_expires_at <= ?""",
                (recovery.job_id, recovery.prior_run_attempt, recovery.phase, timestamp),
            )
            if updated.rowcount != 1:
                raise StaleClaimError("job claim is stale")
            asset = conn.execute(
                """UPDATE assets SET status = 'queued', error = NULL, updated_at = ?
                WHERE id = ? AND status = 'processing'""",
                (timestamp, recovery.asset_id),
            )
            if asset.rowcount != 1:
                raise StaleClaimError("asset status is stale")
            self._add_event_in_connection(
                conn,
                recovery.asset_id,
                "info",
                recovery.phase,
                "Recovered expired job",
                ErrorRecord("recovery", "Recovered expired job"),
                timestamp,
                recovery.prior_run_attempt,
            )
            conn.execute(
                "UPDATE job_recovery_reservations SET state = 'completed', finalized_at = ? WHERE reservation_id = ?",
                (timestamp, reservation["reservation_id"]),
            )
        return RecoveryCompletion(True, False)

    def _transition_in_connection(
        self, conn: sqlite3.Connection, command: ProviderInvocationTransition, timestamp: int
    ) -> bool:
        column = f"{command.to_state}_at"
        metadata = (
            json.dumps(command.provider_metadata.as_dict(), sort_keys=True)
            if command.provider_metadata is not None
            else None
        )
        embedding_space = (
            command.embedding_space.model_dump_json()
            if command.embedding_space is not None
            else None
        )
        updated = conn.execute(
            f"UPDATE provider_invocations SET state = ?, {column} = ?, "
            "cancellation_http_status = COALESCE(?, cancellation_http_status), "
            "error_category = COALESCE(?, error_category), "
            "provider_metadata = COALESCE(?, provider_metadata), "
            "embedding_space = COALESCE(?, embedding_space), timing_ms = COALESCE(?, timing_ms) "
            "WHERE work_item_id = ? AND invocation_attempt = ? AND state = ? AND run_attempt = ?",
            (
                command.to_state,
                timestamp,
                command.cancellation_http_status,
                command.error_category,
                metadata,
                embedding_space,
                command.timing_ms,
                command.work_item_id,
                command.invocation_attempt,
                command.expected_state,
                command.claimant_run_attempt,
            ),
        )
        if updated.rowcount != 1:
            return False
        self._append_transition_in_connection(
            conn,
            command.work_item_id,
            command.invocation_attempt,
            command.expected_state,
            command.to_state,
            command.claimant_run_attempt,
            timestamp,
            command.cancellation_http_status,
        )
        conn.execute(
            "UPDATE provider_work_items SET state = ?, updated_at = ? WHERE work_item_id = ?",
            (command.to_state, timestamp, command.work_item_id),
        )
        return True

    @staticmethod
    def _append_transition_in_connection(
        conn: sqlite3.Connection,
        work_item_id: str,
        invocation_attempt: int,
        from_state: str,
        to_state: str,
        claimant_run_attempt: int,
        timestamp: int,
        cancellation_http_status: int | None = None,
    ) -> None:
        sequence = conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM provider_invocation_transitions "
            "WHERE work_item_id = ? AND invocation_attempt = ?",
            (work_item_id, invocation_attempt),
        ).fetchone()[0]
        conn.execute(
            """INSERT INTO provider_invocation_transitions (work_item_id, invocation_attempt, sequence,
            from_state, to_state, claimant_run_attempt, cancellation_http_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                work_item_id,
                invocation_attempt,
                sequence,
                from_state,
                to_state,
                claimant_run_attempt,
                cancellation_http_status,
                timestamp,
            ),
        )

    def _insert_prepared_provider_invocation(
        self,
        conn: sqlite3.Connection,
        work_item_id: str,
        invocation_attempt: int,
        run_attempt: int,
        correlation_id: str,
        idempotency_key: str,
        request_hash: str,
        duplicate_recovery: bool,
        timestamp: int,
    ) -> None:
        conn.execute(
            """INSERT INTO provider_invocations (work_item_id, invocation_attempt, run_attempt,
            correlation_id, idempotency_key, request_hash, duplicate_recovery, state, prepared_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'prepared', ?)""",
            (
                work_item_id,
                invocation_attempt,
                run_attempt,
                correlation_id,
                idempotency_key,
                request_hash,
                duplicate_recovery,
                timestamp,
            ),
        )
        conn.execute(
            """INSERT INTO provider_invocation_transitions (work_item_id, invocation_attempt, sequence,
            from_state, to_state, claimant_run_attempt, created_at) VALUES (?, ?, 1, NULL, 'prepared', ?, ?)""",
            (work_item_id, invocation_attempt, run_attempt, timestamp),
        )

    def _append_provider_audit_event(
        self,
        conn: sqlite3.Connection,
        work_item_id: str,
        invocation_attempt: int,
        state: str,
        claimant_run_attempt: int,
        category: str | None,
        timestamp: int,
    ) -> None:
        row = conn.execute(
            """SELECT work_item.job_id, work_item.asset_id, work_item.role, work_item.provider_id,
            invocation.correlation_id, invocation.duplicate_recovery FROM provider_work_items AS work_item
            JOIN provider_invocations AS invocation ON invocation.work_item_id = work_item.work_item_id
            WHERE invocation.work_item_id = ? AND invocation.invocation_attempt = ?""",
            (work_item_id, invocation_attempt),
        ).fetchone()
        if row is None:
            raise MigrationStateError("provider invocation is unavailable for audit")
        payload = ErrorRecord(
            category or "provider",
            f"Provider invocation {state}",
            f"{row['provider_id']}:{work_item_id}:{invocation_attempt}",
        )
        self._add_event_in_connection(
            conn,
            str(row["asset_id"]),
            "info",
            "provider invocation",
            f"Provider invocation {state}",
            payload,
            timestamp,
            claimant_run_attempt,
        )

    def _active_entries(
        self, conn: sqlite3.Connection, job_id: str, run_attempt: int
    ) -> list[RecoveryProviderEntry]:
        rows = conn.execute(
            """SELECT invocation.work_item_id, invocation.invocation_attempt, invocation.state,
            invocation.idempotency_key, work_item.role, work_item.provider_id
            FROM provider_invocations AS invocation
            JOIN provider_work_items AS work_item ON work_item.work_item_id = invocation.work_item_id
            WHERE work_item.job_id = ? AND invocation.run_attempt = ?
            AND invocation.state IN ('prepared', 'sent', 'accepted')
            ORDER BY invocation.work_item_id, invocation.invocation_attempt""",
            (job_id, run_attempt),
        ).fetchall()
        return [
            RecoveryProviderEntry(
                str(row["work_item_id"]),
                int(row["invocation_attempt"]),
                run_attempt,
                str(row["state"]),
                None if row["state"] == "prepared" else str(row["idempotency_key"]),
                str(row["role"]),
                str(row["provider_id"]),
            )
            for row in rows
        ]

    @staticmethod
    def _fingerprint(entries: list[RecoveryProviderEntry]) -> str:
        canonical = [
            (
                index,
                entry.work_item_id,
                entry.invocation_attempt,
                entry.expected_state,
                entry.prior_run_attempt,
                entry.role,
                entry.provider_id,
            )
            for index, entry in enumerate(entries)
        ]
        return hashlib.sha256(json.dumps(canonical, separators=(",", ":")).encode()).hexdigest()

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def _now() -> int:
        return int(time.time())

    def _connect(self) -> sqlite3.Connection:
        self._require_open()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        if conn.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() != "wal":
            raise RuntimeError("SQLite WAL mode is unavailable")
        if conn.execute("PRAGMA foreign_keys=ON").fetchone() is not None:
            pass
        if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise RuntimeError("SQLite foreign keys are unavailable")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    @contextmanager
    def _read_connection(self):
        with self._operation_guard():
            conn = self._connect()
            try:
                yield conn
            finally:
                conn.close()

    @contextmanager
    def _transaction(self):
        with self._operation_guard():
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.close()

    @contextmanager
    def _operation_guard(self):
        with self._lifetime_lock:
            self._require_open()
            yield

    def _require_open(self) -> None:
        if self._closed:
            raise DatabaseClosedError("SqliteDatabase is closed")

    @contextmanager
    def _schema_lock(self):
        lock_path = self._db_path.with_suffix(f"{self._db_path.suffix}.schema.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("w") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)
