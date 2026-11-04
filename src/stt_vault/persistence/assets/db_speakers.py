import json
from pathlib import Path

from stt_vault.core.models.records import KnownSpeaker, SpeakerRecord

from ..shared.db_connection import connect, decode_record, now, transaction

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


def _known_speaker(record: dict[str, object] | None) -> KnownSpeaker:
    if record is None:
        raise ValueError("speaker record was missing")
    speaker_id = record.get("id")
    display_name = record.get("display_name")
    centroid = record.get("centroid")
    sample_count = record.get("sample_count")
    created_at = record.get("created_at")
    updated_at = record.get("updated_at")
    if not isinstance(speaker_id, str) or not isinstance(display_name, str):
        raise ValueError("speaker record has invalid identity fields")
    if not isinstance(centroid, list) or any(
        isinstance(value, bool) or not isinstance(value, int | float) for value in centroid
    ):
        raise ValueError("speaker record has an invalid centroid")
    if any(
        isinstance(value, bool) or not isinstance(value, int)
        for value in (sample_count, created_at, updated_at)
    ):
        raise ValueError("speaker record has invalid metadata fields")
    return {
        "id": speaker_id,
        "display_name": display_name,
        "centroid": [float(value) for value in centroid],
        "sample_count": sample_count,
        "created_at": created_at,
        "updated_at": updated_at,
    }
