import json
from pathlib import Path

from stt_vault.core.models.records import PersistedTimedTranscriptUnit, TranscriptSegment
from stt_vault.processing.exports import write_exports


def test_json_export_serializes_persisted_timed_units(tmp_path: Path) -> None:
    exports = write_exports(
        tmp_path,
        "asset-1",
        "clip.wav",
        [
            TranscriptSegment(
                1.0,
                2.0,
                "SPEAKER_00",
                "hello",
                chunk_index=0,
                timed_units=(
                    PersistedTimedTranscriptUnit(
                        "asset-1", 0, 0, "hello", 1000, 1500, 0.9, "en", "word"
                    ),
                ),
            )
        ],
        [],
        ["json"],
    )

    payload = json.loads(Path(exports.json or "").read_text(encoding="utf-8"))
    assert payload[0]["timed_units"] == [
        {
            "asset_id": "asset-1",
            "chunk_index": 0,
            "unit_index": 0,
            "text": "hello",
            "start_ms": 1000,
            "end_ms": 1500,
            "confidence": 0.9,
            "language": "en",
            "token_kind": "word",
        }
    ]
