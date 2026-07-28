from pathlib import Path
from typing import Any

from _support.db_assets import create_processing_asset, initialized_db

from stt_vault.persistence import db


def chunk(
    start: float,
    end: float,
    speaker: str,
    text: str,
    **overrides: Any,
) -> dict[str, Any]:
    data = {
        "start": start,
        "end": end,
        "speaker": speaker,
        "text": text,
    }
    data.update(overrides)
    return data


def test_speaker_operations_propagate_to_chunks_and_asset_json(tmp_path: Path) -> None:
    db_path = create_processing_asset(tmp_path)
    db.update_diarization_metadata(
        db_path,
        "asset-1",
        wav_path=tmp_path / "asset.wav",
        duration=20.0,
        diarization_stats={"ok": True},
        raw_segments=[],
        merged_segments=[],
        speaker_centroids={"SPEAKER_00": [0.2, 0.3]},
    )
    db.upsert_speaker(db_path, "speaker-a", "Alice", [1.0, 3.0], 2)
    db.upsert_speaker(db_path, "speaker-a", "Alice", [3.0, 5.0], 2)
    db.upsert_speaker(db_path, "speaker-b", "Bob", [5.0, 7.0], 1)
    db.upsert_transcript_chunk(
        db_path,
        "asset-1",
        0,
        chunk(
            0.0,
            4.0,
            "SPEAKER_00",
            "hello",
            speaker_id="speaker-a",
            speaker_name="Alice",
            speaker_similarity=0.8,
        ),
        attempts=1,
    )
    db.upsert_transcript_chunk(
        db_path,
        "asset-1",
        1,
        chunk(
            5.0,
            8.0,
            "SPEAKER_01",
            "there",
            speaker_id="speaker-b",
            speaker_name="Bob",
            speaker_similarity=0.6,
        ),
        attempts=1,
    )

    assert db.get_speaker(db_path, "speaker-a")["centroid"] == [2.0, 4.0]
    assert db.get_speaker(db_path, "speaker-a")["sample_count"] == 4
    assert db.find_speaker_by_display_name(db_path, "alice")["id"] == "speaker-a"
    assert db.list_asset_ids_with_speaker_centroids(db_path) == ["asset-1"]

    db.rename_speaker(db_path, "speaker-a", "Alicia")
    renamed_chunks = db.list_transcript_chunks(db_path, "asset-1")
    renamed_asset = db.get_asset(db_path, "asset-1")
    assert renamed_chunks[0]["speaker_name"] == "Alicia"
    assert renamed_asset is not None
    assert renamed_asset["transcript_segments"][0]["speaker_name"] == "Alicia"

    db.relabel_asset_speaker(db_path, "asset-1", "SPEAKER_01", "speaker-a", "Alicia", 0.91)
    relabeled_chunks = db.list_transcript_chunks(db_path, "asset-1")
    assert relabeled_chunks[1]["speaker_id"] == "speaker-a"
    assert relabeled_chunks[1]["speaker_name"] == "Alicia"
    assert relabeled_chunks[1]["speaker_similarity"] == 0.91

    db.relabel_asset_speakers(
        db_path,
        "asset-1",
        {"SPEAKER_00": {"speaker_id": "speaker-b", "display_name": "Bob", "score": 0.72}},
    )
    assert db.list_asset_ids_for_speaker(db_path, "speaker-b") == ["asset-1"]

    db.merge_speakers(db_path, "speaker-b", "speaker-a")
    merged_speaker = db.get_speaker(db_path, "speaker-a")
    merged_chunks = db.list_transcript_chunks(db_path, "asset-1")
    assert db.get_speaker(db_path, "speaker-b") is None
    assert merged_speaker is not None
    assert merged_speaker["sample_count"] == 5
    assert {item["speaker_id"] for item in merged_chunks} == {"speaker-a"}

    db.delete_speaker(db_path, "speaker-a")
    deleted_chunks = db.list_transcript_chunks(db_path, "asset-1")
    deleted_asset = db.get_asset(db_path, "asset-1")
    assert [item["speaker_id"] for item in deleted_chunks] == ["SPEAKER_00", "SPEAKER_01"]
    assert [item["speaker_name"] for item in deleted_chunks] == ["SPEAKER_00", "SPEAKER_01"]
    assert [item["speaker_similarity"] for item in deleted_chunks] == [None, None]
    assert deleted_asset is not None
    assert deleted_asset["transcript_segments"] == deleted_chunks


def test_ai_speaker_names_only_replace_unassigned_local_labels(tmp_path: Path) -> None:
    db_path = initialized_db(tmp_path)
    db.create_asset(db_path, "asset-1", "clip.mp4", "video", tmp_path / "clip.mp4")
    db.upsert_transcript_chunk(
        db_path,
        "asset-1",
        0,
        chunk(0.0, 3.0, "SPEAKER_00", "Welcome"),
        attempts=1,
    )
    db.upsert_transcript_chunk(
        db_path,
        "asset-1",
        1,
        chunk(3.0, 6.0, "SPEAKER_01", "Thanks", speaker_name="Alice"),
        attempts=1,
    )

    applied = db.apply_ai_speaker_names(
        db_path,
        "asset-1",
        {"SPEAKER_00": "Maya Chen", "SPEAKER_01": "Different Name"},
    )
    chunks = db.list_transcript_chunks(db_path, "asset-1")
    asset = db.get_asset(db_path, "asset-1")

    assert applied == {"SPEAKER_00": "Maya Chen"}
    assert [chunk["speaker_name"] for chunk in chunks] == ["Maya Chen", "Alice"]
    assert asset is not None
    assert asset["transcript_segments"] == chunks
