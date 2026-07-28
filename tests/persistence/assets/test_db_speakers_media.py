import sqlite3
from pathlib import Path

import pytest
from _support.db_assets import create_processing_asset, initialized_db
from pydantic import ValidationError

from stt_vault.persistence import db


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


def test_asset_deletion_and_cleanup_task_share_one_transaction(tmp_path: Path) -> None:
    db_path = initialized_db(tmp_path)
    db.create_asset(db_path, "asset-1", "clip.mp4", "video", tmp_path / "clip.mp4")

    db.delete_asset_with_cleanup_task(db_path, "asset-1", tmp_path / "media", tmp_path / "exports")

    assert db.get_asset(db_path, "asset-1") is None
    assert db.get_cleanup_task(db_path, "asset-1") is not None
