import json
from pathlib import Path

from stt_vault.core.api_models import JsonValue
from stt_vault.core.process_diagnostics import format_diagnostic_text
from stt_vault.core.types import ErrorRecord, ExportPaths, SpeakerSegment, TranscriptSegment

from ..shared.db_connection import now, transaction
from .db_job_events import add_event


def mark_failed(db_path: Path, asset_id: str, error: ErrorRecord) -> None:
    _mark_error(db_path, asset_id, error, status="failed", fallback_message="Job failed")


def mark_partial(db_path: Path, asset_id: str, error: ErrorRecord) -> None:
    _mark_error(
        db_path,
        asset_id,
        error,
        status="partial",
        fallback_message="Job partially completed",
    )


def mark_success(
    db_path: Path,
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
    timestamp = now()
    with transaction(db_path) as conn:
        conn.execute(
            """
            UPDATE assets
            SET status = 'success', wav_path = ?, duration = ?, diarization_stats = ?,
                raw_segments = ?, merged_segments = ?, speaker_centroids = ?,
                transcript_segments = ?, exports = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                str(wav_path),
                duration,
                json.dumps(diarization_stats),
                json.dumps(raw_segments),
                json.dumps(merged_segments),
                json.dumps(speaker_centroids),
                json.dumps(transcript_segments),
                json.dumps(exports),
                timestamp,
                asset_id,
            ),
        )
        conn.execute(
            """
            UPDATE jobs
            SET status = 'success', stage = 'done', finished_at = ?, claim_owner = NULL,
                claim_expires_at = NULL
            WHERE asset_id = ?
            """,
            (timestamp, asset_id),
        )
    add_event(db_path, asset_id, "info", "done", "Job completed")


def _mark_error(
    db_path: Path,
    asset_id: str,
    error: ErrorRecord,
    *,
    status: str,
    fallback_message: str,
) -> None:
    error = _persisted_error_record(error)
    payload = json.dumps(error)
    timestamp = now()
    with transaction(db_path) as conn:
        conn.execute(
            """
            UPDATE jobs
            SET status = ?, error = ?, finished_at = ?, claim_owner = NULL,
                claim_expires_at = NULL
            WHERE asset_id = ?
            """,
            (status, payload, timestamp, asset_id),
        )
        conn.execute(
            "UPDATE assets SET status = ?, error = ?, updated_at = ? WHERE id = ?",
            (status, payload, timestamp, asset_id),
        )
    add_event(db_path, asset_id, "error", status, error.get("message", fallback_message), error)


def _persisted_error_record(error: ErrorRecord) -> ErrorRecord:
    """Persist only a bounded, redacted failure category and message."""
    category = error.get("category")
    if not isinstance(category, str) or not category:
        category = "processing"
    message = error.get("message", "Asset processing failed")
    if not isinstance(message, str):
        message = "Asset processing failed"
    return {"category": category[:64], "message": format_diagnostic_text(message)}
