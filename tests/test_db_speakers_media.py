import sqlite3
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from stt_vault.persistence import db

PUBLIC_DB_FUNCTIONS = {
    "connect",
    "transaction",
    "initialize",
    "add_missing_columns",
    "now",
    "row_to_dict",
    "create_asset",
    "create_folder",
    "delete_asset_with_cleanup_task",
    "list_assets",
    "list_folder_tree",
    "list_folders",
    "list_jobs",
    "get_job",
    "get_asset",
    "get_folder",
    "claim_next_job",
    "recover_expired_jobs",
    "renew_job_claim",
    "update_stage",
    "update_progress",
    "add_event",
    "list_events",
    "list_current_run_events",
    "mark_failed",
    "mark_partial",
    "mark_success",
    "update_diarization_metadata",
    "update_asset_exports",
    "update_asset_summary",
    "apply_ai_speaker_names",
    "retry_asset",
    "replace_visual_events",
    "list_visual_events",
    "reset_transcript_chunks",
    "upsert_transcript_chunk",
    "list_transcript_chunks",
    "list_transcript_chunks_from_conn",
    "list_speakers",
    "list_asset_ids_with_speaker_centroids",
    "get_speaker",
    "find_speaker_by_display_name",
    "upsert_speaker",
    "rename_speaker",
    "merge_speakers",
    "move_asset",
    "move_folder",
    "delete_speaker",
    "relabel_asset_speaker",
    "relabel_asset_speakers",
    "list_asset_ids_for_speaker",
    "refresh_asset_transcripts_for_speaker_from_conn",
}


def initialized_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "stt.sqlite3"
    db.initialize(db_path)
    return db_path


def create_processing_asset(tmp_path: Path, asset_id: str = "asset-1") -> Path:
    db_path = initialized_db(tmp_path)
    db.create_asset(db_path, asset_id, "clip.mp4", "video", tmp_path / "clip.mp4")
    assert db.claim_next_job(db_path) == asset_id
    return db_path


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


def test_visual_events_replace_rows_and_appear_in_asset_aggregate(tmp_path: Path) -> None:
    db_path = create_processing_asset(tmp_path)

    db.replace_visual_events(
        db_path,
        "asset-1",
        [
            {"timestamp": 1.25, "score": 0.4},
            {"timestamp": 3.5, "score": 0.9, "kind": "scene_change"},
        ],
    )
    first_events = db.list_visual_events(db_path, "asset-1")
    assert [event["event_index"] for event in first_events] == [0, 1]
    assert first_events[0]["kind"] == "slide_change"
    assert first_events[1]["kind"] == "scene_change"

    db.replace_visual_events(db_path, "asset-1", [{"timestamp": 7.0, "score": 0.8}])
    replaced_events = db.list_visual_events(db_path, "asset-1")
    asset = db.get_asset(db_path, "asset-1")

    assert len(replaced_events) == 1
    assert replaced_events[0]["event_index"] == 0
    assert replaced_events[0]["timestamp"] == 7.0
    assert replaced_events[0]["kind"] == "slide_change"
    assert asset is not None
    assert asset["visual_events"] == replaced_events


def test_persistence_boundaries_reject_malformed_upload_and_visual_event_rows(
    tmp_path: Path,
) -> None:
    db_path = initialized_db(tmp_path)
    db.create_asset(db_path, "asset-1", "clip.wav", "audio", tmp_path / "clip.wav")
    with db.transaction(db_path) as conn:
        conn.execute(
            """
            INSERT INTO upload_sessions (
                id, filename, total_size, offset, temp_path, created_at, updated_at
            )
            VALUES ('upload-1', 'clip.wav', 'not-a-size', 0, '/tmp/upload.part', 1, 1)
            """
        )
        conn.execute(
            """
            INSERT INTO asset_visual_events (
                asset_id, event_index, timestamp, score, kind, created_at
            )
            VALUES ('asset-1', 0, 'not-a-time', 0.2, 'slide_change', 1)
            """
        )

    with pytest.raises(ValidationError):
        db.get_upload_session(db_path, "upload-1")
    with pytest.raises(ValidationError):
        db.list_visual_events(db_path, "asset-1")


def test_job_claim_recovery_preserves_valid_lease_and_requeues_expired_claim(
    tmp_path: Path,
) -> None:
    db_path = initialized_db(tmp_path)
    db.create_asset(db_path, "asset-1", "clip.mp4", "video", tmp_path / "clip.mp4")
    assert db.claim_next_job(db_path, "worker-a", 60) == "asset-1"

    assert db.recover_expired_jobs(db_path) == []
    assert db.renew_job_claim(db_path, "asset-1", "worker-a", 60) is True

    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE jobs SET claim_expires_at = 0 WHERE asset_id = 'asset-1'")

    assert db.recover_expired_jobs(db_path) == ["asset-1"]
    assert db.get_job(db_path, "asset-1").status == "queued"


def test_cleanup_task_and_summary_state_are_persisted(tmp_path: Path) -> None:
    db_path = initialized_db(tmp_path)
    db.create_asset(db_path, "asset-1", "clip.mp4", "video", tmp_path / "clip.mp4")
    db.record_cleanup_task(db_path, "asset-1", tmp_path / "media", tmp_path / "exports")
    assert db.get_cleanup_task(db_path, "asset-1") == {
        "asset_id": "asset-1",
        "media_path": str(tmp_path / "media"),
        "exports_path": str(tmp_path / "exports"),
    }
    db.clear_cleanup_task(db_path, "asset-1")
    assert db.get_cleanup_task(db_path, "asset-1") is None

    db.update_asset_summary(
        db_path, "asset-1", status="success", text="Summary", model="test-model"
    )
    asset = db.get_asset(db_path, "asset-1")
    assert asset is not None
    assert asset["summary_status"] == "success"
    assert asset["summary_text"] == "Summary"


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


def test_asset_deletion_and_cleanup_task_share_one_transaction(tmp_path: Path) -> None:
    db_path = initialized_db(tmp_path)
    db.create_asset(db_path, "asset-1", "clip.mp4", "video", tmp_path / "clip.mp4")

    db.delete_asset_with_cleanup_task(db_path, "asset-1", tmp_path / "media", tmp_path / "exports")

    assert db.get_asset(db_path, "asset-1") is None
    assert db.get_cleanup_task(db_path, "asset-1") is not None
