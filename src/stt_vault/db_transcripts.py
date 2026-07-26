import json
import sqlite3
from pathlib import Path

from .api_models import TranscriptChunkRecord
from .db_connection import connect, decode_record, now, transaction
from .types import ErrorRecord, TranscriptSegment

TRANSCRIPT_CHUNK_JSON_FIELDS = {"error": dict}


def sync_asset_transcript_cache(conn: sqlite3.Connection, asset_id: str, timestamp: int) -> None:
    """Keep the legacy asset JSON cache aligned with normalized chunks."""
    conn.execute(
        "UPDATE assets SET transcript_segments = ?, updated_at = ? WHERE id = ?",
        (json.dumps(list_transcript_chunks_from_conn(conn, asset_id)), timestamp, asset_id),
    )


def reset_transcript_chunks(db_path: Path, asset_id: str) -> None:
    with transaction(db_path) as conn:
        conn.execute("DELETE FROM transcript_chunks WHERE asset_id = ?", (asset_id,))
        sync_asset_transcript_cache(conn, asset_id, now())


def upsert_transcript_chunk(
    db_path: Path,
    asset_id: str,
    chunk_index: int,
    segment: TranscriptSegment,
    *,
    attempts: int,
    status: str = "success",
    error: ErrorRecord | None = None,
) -> None:
    timestamp = now()
    with transaction(db_path) as conn:
        conn.execute(
            """
            INSERT INTO transcript_chunks (
                asset_id,
                chunk_index,
                start,
                end,
                chunk_start,
                chunk_end,
                speaker,
                speaker_id,
                speaker_name,
                speaker_similarity,
                text,
                status,
                attempts,
                error,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(asset_id, chunk_index) DO UPDATE SET
                start = excluded.start,
                end = excluded.end,
                chunk_start = excluded.chunk_start,
                chunk_end = excluded.chunk_end,
                speaker = excluded.speaker,
                speaker_id = excluded.speaker_id,
                speaker_name = excluded.speaker_name,
                speaker_similarity = excluded.speaker_similarity,
                text = excluded.text,
                status = excluded.status,
                attempts = excluded.attempts,
                error = excluded.error,
                updated_at = excluded.updated_at
            """,
            (
                asset_id,
                chunk_index,
                segment["start"],
                segment["end"],
                segment.get("chunk_start", segment["start"]),
                segment.get("chunk_end", segment["end"]),
                segment["speaker"],
                segment.get("speaker_id"),
                segment.get("speaker_name"),
                segment.get("speaker_similarity"),
                segment["text"],
                status,
                attempts,
                json.dumps(error) if error is not None else None,
                timestamp,
            ),
        )
        sync_asset_transcript_cache(conn, asset_id, timestamp)


def list_transcript_chunks(db_path: Path, asset_id: str) -> list[TranscriptSegment]:
    with connect(db_path) as conn:
        return list_transcript_chunks_from_conn(conn, asset_id)


def list_transcript_chunks_from_conn(
    conn: sqlite3.Connection,
    asset_id: str,
) -> list[TranscriptSegment]:
    rows = conn.execute(
        """
        SELECT *
        FROM transcript_chunks
        WHERE asset_id = ?
        ORDER BY chunk_index ASC
        """,
        (asset_id,),
    ).fetchall()
    chunks: list[TranscriptSegment] = []
    for row in rows:
        record = decode_record(row, json_fields=TRANSCRIPT_CHUNK_JSON_FIELDS)
        if record is None:
            raise ValueError("transcript chunk record has an invalid shape")
        try:
            chunk = TranscriptChunkRecord.model_validate(record).model_dump(exclude={"asset_id"})
            error = record.get("error")
            if isinstance(error, dict) and "category" not in error and chunk["error"] is not None:
                chunk["error"].pop("category", None)
            chunks.append(chunk)
        except ValueError as error:
            raise ValueError("transcript chunk record has an invalid shape") from error
    return chunks
