import json
import sqlite3
from pathlib import Path

from stt_vault.core.types import KnownSpeaker, SpeakerMatch, SpeakerRecord

from .db_connection import connect, decode_record, now, transaction
from .db_transcripts import sync_asset_transcript_cache

SPEAKER_JSON_FIELDS = {"centroid": list}


def list_speakers(db_path: Path) -> list[KnownSpeaker]:
    with connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM speakers ORDER BY display_name").fetchall()
    return [_known_speaker(decode_record(row, json_fields=SPEAKER_JSON_FIELDS)) for row in rows]


def list_asset_ids_with_speaker_centroids(db_path: Path) -> list[str]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id
            FROM assets
            WHERE speaker_centroids IS NOT NULL
            ORDER BY updated_at DESC
            """
        ).fetchall()
    return [row["id"] for row in rows]


def get_speaker(db_path: Path, speaker_id: str) -> SpeakerRecord | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM speakers WHERE id = ?", (speaker_id,)).fetchone()
    if row is None:
        return None
    return decode_record(row, json_fields=SPEAKER_JSON_FIELDS)


def find_speaker_by_display_name(db_path: Path, display_name: str) -> SpeakerRecord | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM speakers WHERE lower(display_name) = lower(?)",
            (display_name,),
        ).fetchone()
    if row is None:
        return None
    return decode_record(row, json_fields=SPEAKER_JSON_FIELDS)


def upsert_speaker(
    db_path: Path,
    speaker_id: str,
    display_name: str,
    centroid: list[float],
    sample_count: int,
) -> None:
    timestamp = now()
    with transaction(db_path) as conn:
        existing = conn.execute("SELECT * FROM speakers WHERE id = ?", (speaker_id,)).fetchone()
        if existing is not None:
            existing_centroid = decode_record(existing, json_fields=SPEAKER_JSON_FIELDS)["centroid"]
            existing_count = max(1, int(existing["sample_count"]))
            incoming_count = max(1, sample_count)
            if len(existing_centroid) == len(centroid):
                total = existing_count + incoming_count
                centroid = [
                    ((float(old) * existing_count) + (float(new) * incoming_count)) / total
                    for old, new in zip(existing_centroid, centroid, strict=False)
                ]
                sample_count = total
        conn.execute(
            """
            INSERT INTO speakers (id, display_name, centroid, sample_count, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                display_name = excluded.display_name,
                centroid = excluded.centroid,
                sample_count = excluded.sample_count,
                updated_at = excluded.updated_at
            """,
            (speaker_id, display_name, json.dumps(centroid), sample_count, timestamp, timestamp),
        )


def rename_speaker(db_path: Path, speaker_id: str, display_name: str) -> None:
    timestamp = now()
    with transaction(db_path) as conn:
        conn.execute(
            "UPDATE speakers SET display_name = ?, updated_at = ? WHERE id = ?",
            (display_name, timestamp, speaker_id),
        )
        conn.execute(
            """
            UPDATE transcript_chunks
            SET speaker_name = ?, updated_at = ?
            WHERE speaker_id = ?
            """,
            (display_name, timestamp, speaker_id),
        )
        refresh_asset_transcripts_for_speaker_from_conn(conn, speaker_id, timestamp)


def merge_speakers(db_path: Path, source_speaker_id: str, target_speaker_id: str) -> None:
    if source_speaker_id == target_speaker_id:
        return

    timestamp = now()
    with transaction(db_path) as conn:
        source = conn.execute(
            "SELECT * FROM speakers WHERE id = ?",
            (source_speaker_id,),
        ).fetchone()
        target = conn.execute(
            "SELECT * FROM speakers WHERE id = ?",
            (target_speaker_id,),
        ).fetchone()
        if source is None or target is None:
            raise KeyError(source_speaker_id if source is None else target_speaker_id)

        source_centroid = decode_record(source, json_fields=SPEAKER_JSON_FIELDS)["centroid"]
        target_centroid = decode_record(target, json_fields=SPEAKER_JSON_FIELDS)["centroid"]
        source_count = max(1, int(source["sample_count"]))
        target_count = max(1, int(target["sample_count"]))
        merged_centroid = target_centroid
        merged_count = target_count + source_count
        if len(source_centroid) == len(target_centroid):
            merged_centroid = [
                ((float(target_value) * target_count) + (float(source_value) * source_count))
                / merged_count
                for target_value, source_value in zip(
                    target_centroid,
                    source_centroid,
                    strict=False,
                )
            ]

        rows = conn.execute(
            "SELECT DISTINCT asset_id FROM transcript_chunks WHERE speaker_id IN (?, ?)",
            (source_speaker_id, target_speaker_id),
        ).fetchall()
        conn.execute(
            """
            UPDATE speakers
            SET centroid = ?,
                sample_count = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (json.dumps(merged_centroid), merged_count, timestamp, target_speaker_id),
        )
        conn.execute("DELETE FROM speakers WHERE id = ?", (source_speaker_id,))
        conn.execute(
            """
            UPDATE transcript_chunks
            SET speaker_id = ?,
                speaker_name = ?,
                updated_at = ?
            WHERE speaker_id = ?
            """,
            (target_speaker_id, target["display_name"], timestamp, source_speaker_id),
        )
        for row in rows:
            asset_id = row["asset_id"]
            sync_asset_transcript_cache(conn, asset_id, timestamp)


def delete_speaker(db_path: Path, speaker_id: str) -> None:
    timestamp = now()
    with transaction(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT asset_id FROM transcript_chunks WHERE speaker_id = ?",
            (speaker_id,),
        ).fetchall()
        conn.execute("DELETE FROM speakers WHERE id = ?", (speaker_id,))
        conn.execute(
            """
            UPDATE transcript_chunks
            SET speaker_id = speaker,
                speaker_name = speaker,
                speaker_similarity = NULL,
                updated_at = ?
            WHERE speaker_id = ?
            """,
            (timestamp, speaker_id),
        )
        for row in rows:
            asset_id = row["asset_id"]
            sync_asset_transcript_cache(conn, asset_id, timestamp)


def relabel_asset_speaker(
    db_path: Path,
    asset_id: str,
    local_speaker: str,
    speaker_id: str,
    display_name: str,
    similarity: float | None,
) -> None:
    timestamp = now()
    with transaction(db_path) as conn:
        conn.execute(
            """
            UPDATE transcript_chunks
            SET speaker_id = ?,
                speaker_name = ?,
                speaker_similarity = ?,
                updated_at = ?
            WHERE asset_id = ? AND speaker = ?
            """,
            (speaker_id, display_name, similarity, timestamp, asset_id, local_speaker),
        )
        sync_asset_transcript_cache(conn, asset_id, timestamp)


def relabel_asset_speakers(
    db_path: Path,
    asset_id: str,
    matches: dict[str, SpeakerMatch],
) -> None:
    timestamp = now()
    with transaction(db_path) as conn:
        for local_speaker, match in matches.items():
            conn.execute(
                """
                UPDATE transcript_chunks
                SET speaker_id = ?,
                    speaker_name = ?,
                    speaker_similarity = ?,
                    updated_at = ?
                WHERE asset_id = ? AND speaker = ?
                """,
                (
                    match.get("speaker_id", local_speaker),
                    match.get("display_name", local_speaker),
                    match.get("score"),
                    timestamp,
                    asset_id,
                    local_speaker,
                ),
            )
        sync_asset_transcript_cache(conn, asset_id, timestamp)


def list_asset_ids_for_speaker(db_path: Path, speaker_id: str) -> list[str]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT asset_id FROM transcript_chunks WHERE speaker_id = ?",
            (speaker_id,),
        ).fetchall()
    return [row["asset_id"] for row in rows]


def refresh_asset_transcripts_for_speaker_from_conn(
    conn: sqlite3.Connection,
    speaker_id: str,
    timestamp: int,
) -> None:
    rows = conn.execute(
        "SELECT DISTINCT asset_id FROM transcript_chunks WHERE speaker_id = ?",
        (speaker_id,),
    ).fetchall()
    for row in rows:
        asset_id = row["asset_id"]
        sync_asset_transcript_cache(conn, asset_id, timestamp)


def _known_speaker(record: dict[str, object] | None) -> KnownSpeaker:
    if record is None:
        raise ValueError("speaker record was missing")
    speaker_id = record.get("id")
    display_name = record.get("display_name")
    centroid = record.get("centroid")
    if not isinstance(speaker_id, str) or not isinstance(display_name, str):
        raise ValueError("speaker record has invalid identity fields")
    if not isinstance(centroid, list) or any(
        isinstance(value, bool) or not isinstance(value, int | float) for value in centroid
    ):
        raise ValueError("speaker record has an invalid centroid")
    return {
        "id": speaker_id,
        "display_name": display_name,
        "centroid": [float(value) for value in centroid],
    }
