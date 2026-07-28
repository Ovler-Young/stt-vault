import json
from pathlib import Path

from stt_vault.core.api_models import JsonValue
from stt_vault.core.types import ExportPaths, SpeakerSegment

from ..shared.db_connection import now, transaction


def update_diarization_metadata(
    db_path: Path,
    asset_id: str,
    *,
    wav_path: Path,
    duration: float,
    diarization_stats: dict[str, JsonValue],
    raw_segments: list[SpeakerSegment],
    merged_segments: list[SpeakerSegment],
    speaker_centroids: dict[str, list[float]],
) -> None:
    timestamp = now()
    with transaction(db_path) as conn:
        conn.execute(
            """
            UPDATE assets
            SET wav_path = ?, duration = ?, diarization_stats = ?, raw_segments = ?,
                merged_segments = ?, speaker_centroids = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                str(wav_path),
                duration,
                json.dumps(diarization_stats),
                json.dumps(raw_segments),
                json.dumps(merged_segments),
                json.dumps(speaker_centroids),
                timestamp,
                asset_id,
            ),
        )


def update_asset_exports(db_path: Path, asset_id: str, exports: ExportPaths) -> None:
    with transaction(db_path) as conn:
        conn.execute(
            "UPDATE assets SET exports = ?, updated_at = ? WHERE id = ?",
            (json.dumps(exports), now(), asset_id),
        )
