import sqlite3
from pathlib import Path
from typing import Any

import pytest

from stt_vault.core.api_models import EventResponse, JobResponse
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
    "asset_exists",
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


def test_folder_tree_and_asset_moves_preserve_a_single_hierarchy(tmp_path: Path) -> None:
    db_path = initialized_db(tmp_path)
    root = db.create_folder(db_path, "Meetings")
    child = db.create_folder(db_path, "Planning", parent_id=root.id)
    db.create_asset(
        db_path,
        "asset-1",
        "roadmap.wav",
        "audio",
        tmp_path / "roadmap.wav",
        parent_folder_id=child.id,
    )
    db.create_asset(
        db_path,
        "asset-2",
        "inbox.wav",
        "audio",
        tmp_path / "inbox.wav",
    )

    tree = db.list_folder_tree(db_path)

    assert [asset.id for asset in tree.assets] == ["asset-2"]
    [tree_root] = tree.folders
    assert tree_root.id == root.id
    assert tree_root.assets == []
    [tree_child] = tree_root.children
    assert tree_child.id == child.id
    assert [asset.id for asset in tree_child.assets] == ["asset-1"]

    moved_asset = db.move_asset(db_path, "asset-2", child.id)
    moved_folder = db.move_folder(db_path, child.id, None)

    assert moved_asset["parent_folder_id"] == child.id
    assert moved_folder.parent_id is None
    assert db.get_asset(db_path, "asset-2")["parent_folder_id"] == child.id
    moved_folder_record = db.get_folder(db_path, child.id)
    assert moved_folder_record is not None
    assert moved_folder_record.parent_id is None


def test_folder_moves_reject_missing_parents_and_descendant_cycles(tmp_path: Path) -> None:
    db_path = initialized_db(tmp_path)
    root = db.create_folder(db_path, "Root")
    child = db.create_folder(db_path, "Child", parent_id=root.id)
    grandchild = db.create_folder(db_path, "Grandchild", parent_id=child.id)
    db.create_asset(db_path, "asset-1", "clip.wav", "audio", tmp_path / "clip.wav")

    with pytest.raises(KeyError):
        db.create_folder(db_path, "Missing parent", parent_id="missing")
    with pytest.raises(KeyError):
        db.move_folder(db_path, child.id, "missing")
    with pytest.raises(KeyError):
        db.move_asset(db_path, "asset-1", "missing")
    with pytest.raises(ValueError, match="descendant"):
        db.move_folder(db_path, root.id, grandchild.id)
    with pytest.raises(ValueError, match="itself"):
        db.move_folder(db_path, child.id, child.id)

    root_record = db.get_folder(db_path, root.id)
    child_record = db.get_folder(db_path, child.id)
    grandchild_record = db.get_folder(db_path, grandchild.id)
    assert root_record is not None
    assert child_record is not None
    assert grandchild_record is not None
    assert root_record.parent_id is None
    assert child_record.parent_id == root.id
    assert grandchild_record.parent_id == child.id


def test_asset_job_lifecycle_and_get_asset_aggregate(tmp_path: Path) -> None:
    db_path = initialized_db(tmp_path)
    db.create_asset(db_path, "asset-1", "clip.mp4", "video", tmp_path / "clip.mp4")

    [listed_asset] = db.list_assets(db_path)
    [listed_job] = db.list_jobs(db_path)
    assert listed_asset["status"] == "queued"
    assert listed_asset["filename"] == "clip.mp4"
    assert listed_job.asset_id == "asset-1"
    assert listed_job.filename == "clip.mp4"
    assert isinstance(listed_job, JobResponse)

    assert db.claim_next_job(db_path) == "asset-1"
    db.update_stage(db_path, "asset-1", "transcribing speech")
    db.update_progress(
        db_path,
        "asset-1",
        total_chunks=3,
        done_chunks=1,
        failed_chunks=1,
        next_retry_at=12345,
    )
    db.add_event(
        db_path,
        "asset-1",
        "warning",
        "transcribing speech",
        "Chunk retry scheduled",
        {"chunk_index": 1},
    )
    event = db.list_events(db_path, "asset-1")[-1]
    assert isinstance(event, EventResponse)
    assert event.message == "Chunk retry scheduled"

    asset = db.get_asset(db_path, "asset-1")

    assert asset is not None
    assert asset["status"] == "processing"
    assert asset["job"]["stage"] == "transcribing speech"
    assert asset["job"]["run_attempt"] == 1
    assert asset["job"]["progress_total_chunks"] == 3
    assert asset["job"]["progress_done_chunks"] == 1
    assert asset["job"]["progress_failed_chunks"] == 1
    assert asset["job"]["next_retry_at"] == 12345
    assert [event["message"] for event in asset["events"]] == [
        "transcribing speech",
        "Chunk retry scheduled",
    ]
    assert asset["events"][1]["payload"] == {"chunk_index": 1}


def test_get_asset_does_not_load_full_event_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = create_processing_asset(tmp_path)
    db.add_event(db_path, "asset-1", "info", "transcribing", "progress")

    def fail_if_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("get_asset must not load event history")

    monkeypatch.setattr(
        "stt_vault.persistence.db_assets.list_events", fail_if_called, raising=False
    )

    asset = db.get_asset(db_path, "asset-1")

    assert asset is not None
    assert "event_history" not in asset


def test_transcript_chunks_are_ordered_decoded_and_mirrored(tmp_path: Path) -> None:
    db_path = create_processing_asset(tmp_path)

    db.upsert_transcript_chunk(
        db_path,
        "asset-1",
        1,
        chunk(10.0, 12.0, "SPEAKER_01", "second", chunk_start=9.5, chunk_end=12.5),
        attempts=1,
    )
    db.upsert_transcript_chunk(
        db_path,
        "asset-1",
        0,
        chunk(0.0, 2.0, "SPEAKER_00", "first"),
        attempts=2,
        status="failed",
        error={"message": "rate limited"},
    )

    chunks = db.list_transcript_chunks(db_path, "asset-1")
    asset = db.get_asset(db_path, "asset-1")

    assert [item["chunk_index"] for item in chunks] == [0, 1]
    assert chunks[0]["chunk_start"] == 0.0
    assert chunks[0]["chunk_end"] == 2.0
    assert chunks[0]["error"] == {"message": "rate limited"}
    assert chunks[1]["chunk_start"] == 9.5
    assert chunks[1]["chunk_end"] == 12.5
    assert asset is not None
    assert asset["transcript_segments"] == chunks

    db.reset_transcript_chunks(db_path, "asset-1")

    assert db.list_transcript_chunks(db_path, "asset-1") == []


def test_transcript_chunk_boundary_rejects_malformed_database_record(tmp_path: Path) -> None:
    db_path = create_processing_asset(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO transcript_chunks
                (asset_id, chunk_index, start, end, chunk_start, chunk_end,
                 speaker, text, status, attempts, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("asset-1", 0, "not-a-time", 1.0, 0.0, 1.0, "SPEAKER_00", "hello", "success", 1, 0),
        )

    with pytest.raises(ValueError, match="invalid shape"):
        db.list_transcript_chunks(db_path, "asset-1")


def test_success_failure_partial_and_retry_transitions(tmp_path: Path) -> None:
    success_path = create_processing_asset(tmp_path / "success", "success-asset")
    db.mark_success(
        success_path,
        "success-asset",
        wav_path=tmp_path / "success.wav",
        duration=12.5,
        diarization_stats={"segments": 2},
        raw_segments=[{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}],
        merged_segments=[{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}],
        speaker_centroids={"SPEAKER_00": [0.1, 0.2]},
        transcript_segments=[{"speaker": "SPEAKER_00", "text": "hello"}],
        exports={"txt": "/tmp/success.txt"},
    )
    success_asset = db.get_asset(success_path, "success-asset")
    assert success_asset is not None
    assert success_asset["status"] == "success"
    assert success_asset["job"]["status"] == "success"
    assert success_asset["job"]["stage"] == "done"
    assert success_asset["exports"] == {"txt": "/tmp/success.txt"}
    assert success_asset["diarization_stats"] == {"segments": 2}
    assert success_asset["raw_segments"] == [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]

    failed_path = create_processing_asset(tmp_path / "failed", "failed-asset")
    db.update_progress(failed_path, "failed-asset", total_chunks=4, done_chunks=2, failed_chunks=1)
    db.mark_failed(
        failed_path,
        "failed-asset",
        {"category": "processing", "message": "boom OPENAI_API_KEY=secret /srv/private.wav"},
    )
    failed_asset = db.get_asset(failed_path, "failed-asset")
    assert failed_asset is not None
    assert failed_asset["status"] == "failed"
    assert failed_asset["error"] == {
        "category": "processing",
        "message": "boom [redacted] [path]",
    }
    assert failed_asset["job"]["error"] == failed_asset["error"]

    db.retry_asset(failed_path, "failed-asset")
    retried_asset = db.get_asset(failed_path, "failed-asset")
    assert retried_asset is not None
    assert retried_asset["status"] == "queued"
    assert retried_asset["error"] is None
    assert retried_asset["job"]["status"] == "queued"
    assert retried_asset["job"]["error"] is None
    assert retried_asset["job"]["run_attempt"] == 1
    assert retried_asset["job"]["progress_total_chunks"] == 0
    assert retried_asset["job"]["progress_done_chunks"] == 0
    assert retried_asset["job"]["progress_failed_chunks"] == 0
    assert db.list_events(failed_path, "failed-asset")[-1].run_attempt == 2

    assert db.claim_next_job(failed_path) == "failed-asset"
    retry_run_asset = db.get_asset(failed_path, "failed-asset")
    assert retry_run_asset is not None
    assert [event["message"] for event in retry_run_asset["events"]] == ["Job queued for retry"]

    partial_path = create_processing_asset(tmp_path / "partial", "partial-asset")
    db.mark_partial(
        partial_path,
        "partial-asset",
        {"category": "provider", "message": "slow"},
    )
    partial_asset = db.get_asset(partial_path, "partial-asset")
    assert partial_asset is not None
    assert partial_asset["status"] == "partial"
    assert partial_asset["job"]["status"] == "partial"
    assert partial_asset["error"] == {"category": "provider", "message": "slow"}
