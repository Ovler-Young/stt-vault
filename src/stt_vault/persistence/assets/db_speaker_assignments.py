import json
from pathlib import Path

from stt_vault.core.models.records import SpeakerMatch

from ..shared.db_connection import connect, decode_record, now, transaction
from .db_speakers import SPEAKER_JSON_FIELDS
from .db_transcripts import list_transcript_chunks_from_conn


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
        _update_display(
            conn,
            timestamp,
            lambda s: (
                {**s, "speaker_name": display_name} if s.get("speaker_id") == speaker_id else s
            ),
        )


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

        conn.execute(
            """
            UPDATE speakers
            SET centroid = ?, sample_count = ?, updated_at = ?
            WHERE id = ?
            """,
            (json.dumps(merged_centroid), merged_count, timestamp, target_speaker_id),
        )
        conn.execute("DELETE FROM speakers WHERE id = ?", (source_speaker_id,))
        conn.execute(
            """
            UPDATE transcript_chunks
            SET speaker_id = ?, speaker_name = ?, updated_at = ?
            WHERE speaker_id = ?
            """,
            (target_speaker_id, target["display_name"], timestamp, source_speaker_id),
        )
        _update_display(
            conn,
            timestamp,
            lambda s: (
                {**s, "speaker_id": target_speaker_id, "speaker_name": target["display_name"]}
                if s.get("speaker_id") == source_speaker_id
                else s
            ),
        )


def delete_speaker(db_path: Path, speaker_id: str) -> None:
    timestamp = now()
    with transaction(db_path) as conn:
        conn.execute("DELETE FROM speakers WHERE id = ?", (speaker_id,))
        conn.execute(
            """
            UPDATE transcript_chunks
            SET speaker_id = speaker, speaker_name = speaker,
                speaker_similarity = NULL, updated_at = ?
            WHERE speaker_id = ?
            """,
            (timestamp, speaker_id),
        )
        _update_display(
            conn,
            timestamp,
            lambda s: (
                {
                    **s,
                    "speaker_id": s["speaker"],
                    "speaker_name": s["speaker"],
                    "speaker_similarity": None,
                }
                if s.get("speaker_id") == speaker_id
                else s
            ),
        )


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
            SET speaker_id = ?, speaker_name = ?, speaker_similarity = ?, updated_at = ?
            WHERE asset_id = ? AND speaker = ?
            """,
            (speaker_id, display_name, similarity, timestamp, asset_id, local_speaker),
        )
        _update_display(
            conn,
            timestamp,
            lambda s: (
                {
                    **s,
                    "speaker_id": speaker_id,
                    "speaker_name": display_name,
                    "speaker_similarity": similarity,
                }
                if s["speaker"] == local_speaker
                else s
            ),
            asset_id,
        )


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
                SET speaker_id = ?, speaker_name = ?, speaker_similarity = ?, updated_at = ?
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
        _update_display(
            conn,
            timestamp,
            lambda s: (
                {
                    **s,
                    "speaker_id": matches[s["speaker"]].get("speaker_id", s["speaker"]),
                    "speaker_name": matches[s["speaker"]].get("display_name", s["speaker"]),
                    "speaker_similarity": matches[s["speaker"]].get("score"),
                }
                if s["speaker"] in matches
                else s
            ),
            asset_id,
        )


def list_asset_ids_for_speaker(db_path: Path, speaker_id: str) -> list[str]:
    with connect(db_path) as conn:
        return [
            row["id"]
            for row in conn.execute("SELECT id, transcript_segments FROM assets")
            if any(
                s.get("speaker_id") == speaker_id
                for s in json.loads(row["transcript_segments"] or "[]")
            )
        ]


def _update_display(conn, timestamp, transform, asset_id=None):
    rows = conn.execute(
        "SELECT id, transcript_segments FROM assets"
        if asset_id is None
        else "SELECT id, transcript_segments FROM assets WHERE id = ?",
        () if asset_id is None else (asset_id,),
    ).fetchall()
    for row in rows:
        segments = json.loads(
            row["transcript_segments"] or "[]"
        ) or list_transcript_chunks_from_conn(conn, row["id"])
        updated = [transform(segment) for segment in segments]
        conn.execute(
            "UPDATE assets SET transcript_segments = ?, updated_at = ? WHERE id = ?",
            (json.dumps(updated), timestamp, row["id"]),
        )


def refresh_asset_transcripts_for_speaker_from_conn(conn, speaker_id, timestamp):
    speaker = conn.execute(
        "SELECT display_name FROM speakers WHERE id = ?", (speaker_id,)
    ).fetchone()
    if speaker is not None:
        _update_display(
            conn,
            timestamp,
            lambda s: (
                {**s, "speaker_name": speaker["display_name"]}
                if s.get("speaker_id") == speaker_id
                else s
            ),
        )
