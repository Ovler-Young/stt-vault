import sqlite3
from pathlib import Path
from typing import Any

from stt_vault import db

PUBLIC_DB_FUNCTIONS = {
    "connect",
    "transaction",
    "initialize",
    "add_missing_columns",
    "now",
    "row_to_dict",
    "create_asset",
    "list_assets",
    "list_jobs",
    "get_job",
    "get_asset",
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


def test_db_facade_preserves_public_import_surface() -> None:
    missing = [
        name for name in sorted(PUBLIC_DB_FUNCTIONS) if not callable(getattr(db, name, None))
    ]

    assert missing == []


def test_initialize_schema_is_idempotent_and_upgrades_legacy_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "schema.sqlite3"
    db.initialize(db_path)
    db.initialize(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        indexes = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        assets_columns = {row["name"] for row in conn.execute("PRAGMA table_info(assets)")}
        jobs_columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
        events_columns = {row["name"] for row in conn.execute("PRAGMA table_info(job_events)")}
        chunk_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(transcript_chunks)")
        }

    assert {
        "assets",
        "speakers",
        "jobs",
        "job_events",
        "transcript_chunks",
        "asset_visual_events",
    }.issubset(tables)
    assert {
        "idx_assets_created_at",
        "idx_jobs_status_created_at",
        "idx_job_events_asset_created_at",
        "idx_transcript_chunks_asset_index",
        "idx_visual_events_asset_index",
    }.issubset(indexes)
    assert {
        "diarization_stats",
        "raw_segments",
        "merged_segments",
        "speaker_centroids",
        "transcript_segments",
        "exports",
    }.issubset(assets_columns)
    assert {
        "progress_total_chunks",
        "progress_done_chunks",
        "progress_failed_chunks",
        "next_retry_at",
        "run_attempt",
    }.issubset(jobs_columns)
    assert {"run_attempt"}.issubset(events_columns)
    assert {"chunk_start", "chunk_end", "speaker_id", "speaker_name"}.issubset(chunk_columns)

    legacy_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(legacy_path) as conn:
        conn.executescript(
            """
            CREATE TABLE assets (
                id TEXT PRIMARY KEY,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY,
                asset_id TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE job_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                asset_id TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            """
        )

    db.initialize(legacy_path)

    with sqlite3.connect(legacy_path) as conn:
        conn.row_factory = sqlite3.Row
        legacy_jobs_columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
        legacy_events_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(job_events)")
        }

    assert {
        "progress_total_chunks",
        "progress_done_chunks",
        "progress_failed_chunks",
        "next_retry_at",
        "run_attempt",
    }.issubset(legacy_jobs_columns)
    assert "run_attempt" in legacy_events_columns


def test_asset_job_lifecycle_and_get_asset_aggregate(tmp_path: Path) -> None:
    db_path = initialized_db(tmp_path)
    db.create_asset(db_path, "asset-1", "clip.mp4", "video", tmp_path / "clip.mp4")

    [listed_asset] = db.list_assets(db_path)
    [listed_job] = db.list_jobs(db_path)
    assert listed_asset["status"] == "queued"
    assert listed_asset["filename"] == "clip.mp4"
    assert listed_job["asset_id"] == "asset-1"
    assert listed_job["filename"] == "clip.mp4"

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
    assert asset["event_history"] == asset["events"]
    assert asset["events"][1]["payload"] == {"chunk_index": 1}


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


def test_success_failure_partial_and_retry_transitions(tmp_path: Path) -> None:
    success_path = create_processing_asset(tmp_path / "success", "success-asset")
    db.mark_success(
        success_path,
        "success-asset",
        wav_path=tmp_path / "success.wav",
        duration=12.5,
        diarization_stats={"segments": 2},
        raw_segments=[{"speaker": "SPEAKER_00"}],
        merged_segments=[{"speaker": "SPEAKER_00", "text": "hello"}],
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
    assert success_asset["raw_segments"] == [{"speaker": "SPEAKER_00"}]

    failed_path = create_processing_asset(tmp_path / "failed", "failed-asset")
    db.update_progress(failed_path, "failed-asset", total_chunks=4, done_chunks=2, failed_chunks=1)
    db.mark_failed(failed_path, "failed-asset", {"type": "RuntimeError", "message": "boom"})
    failed_asset = db.get_asset(failed_path, "failed-asset")
    assert failed_asset is not None
    assert failed_asset["status"] == "failed"
    assert failed_asset["error"] == {"type": "RuntimeError", "message": "boom"}
    assert failed_asset["job"]["error"] == {"type": "RuntimeError", "message": "boom"}

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
    assert retried_asset["event_history"][-1]["run_attempt"] == 2

    assert db.claim_next_job(failed_path) == "failed-asset"
    retry_run_asset = db.get_asset(failed_path, "failed-asset")
    assert retry_run_asset is not None
    assert [event["message"] for event in retry_run_asset["events"]] == ["Job queued for retry"]

    partial_path = create_processing_asset(tmp_path / "partial", "partial-asset")
    db.mark_partial(partial_path, "partial-asset", {"type": "TimeoutError", "message": "slow"})
    partial_asset = db.get_asset(partial_path, "partial-asset")
    assert partial_asset is not None
    assert partial_asset["status"] == "partial"
    assert partial_asset["job"]["status"] == "partial"
    assert partial_asset["error"] == {"type": "TimeoutError", "message": "slow"}


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
    assert db.get_job(db_path, "asset-1")["status"] == "queued"


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
