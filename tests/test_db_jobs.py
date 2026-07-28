import sqlite3
from pathlib import Path

import pytest

from stt_vault.persistence import db
from stt_vault.persistence.jobs.db_job_queue import parse_lease_expiration


def create_asset(db_path: Path, asset_id: str = "asset-1") -> None:
    db.create_asset(db_path, asset_id, f"{asset_id}.wav", "audio", db_path.parent / "clip.wav")


@pytest.mark.parametrize("lease_seconds", [0, -1, True, 1.5])
def test_queue_lease_validation_rejects_non_positive_integers(
    tmp_path: Path, lease_seconds: int | float
) -> None:
    db_path = tmp_path / "stt.sqlite3"
    db.initialize(db_path)
    create_asset(db_path)

    with pytest.raises(ValueError, match="lease_seconds must be a positive integer"):
        db.claim_next_job(db_path, lease_seconds=lease_seconds)


@pytest.mark.parametrize(
    ("lease_expiration", "recovered"),
    [
        pytest.param(2_000_000_000.5, True, id="fractional-future"),
        pytest.param(float("nan"), True, id="nan"),
        pytest.param(float("inf"), True, id="positive-infinity"),
        pytest.param(float("-inf"), True, id="negative-infinity"),
        pytest.param(True, True, id="boolean"),
        pytest.param("not-a-timestamp", True, id="malformed-string"),
        pytest.param(b"not-a-timestamp", True, id="malformed-bytes"),
        pytest.param(2_000_000_000, False, id="integer-future"),
    ],
)
def test_persisted_lease_expiration_recovery_is_lossless(
    tmp_path: Path, lease_expiration: object, recovered: bool
) -> None:
    db_path = tmp_path / "stt.sqlite3"
    db.initialize(db_path)
    create_asset(db_path)

    assert db.claim_next_job(db_path, "worker-a", 60) == "asset-1"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE jobs SET claim_expires_at = ? WHERE asset_id = ?",
            (lease_expiration, "asset-1"),
        )

    assert db.recover_expired_jobs(db_path) == (["asset-1"] if recovered else [])
    job = db.get_job(db_path, "asset-1")
    assert job is not None
    if recovered:
        assert job.status == "queued"
        assert job.claim_owner is None
        assert job.claim_expires_at is None
        assert [event.message for event in db.list_events(db_path, "asset-1")] == [
            "Recovered interrupted job"
        ]
    else:
        assert job.status == "processing"
        assert job.claim_owner == "worker-a"
        assert job.claim_expires_at == lease_expiration
        assert db.list_events(db_path, "asset-1") == []


@pytest.mark.parametrize(
    "lease_expiration",
    [True, 2_000_000_000.5, float("nan"), float("inf"), float("-inf")],
)
def test_parse_lease_expiration_rejects_lossy_values(lease_expiration: object) -> None:
    assert parse_lease_expiration(lease_expiration) is None


def test_progress_and_current_run_events_are_persisted_in_creation_order(tmp_path: Path) -> None:
    db_path = tmp_path / "stt.sqlite3"
    db.initialize(db_path)
    create_asset(db_path)
    assert db.claim_next_job(db_path) == "asset-1"

    db.update_stage(db_path, "asset-1", "transcribing")
    db.update_progress(db_path, "asset-1", total_chunks=3, done_chunks=1, next_retry_at=42)
    db.add_event(db_path, "asset-1", "warning", "transcribing", "Chunk retry", {"chunk": 1})

    job = db.get_job(db_path, "asset-1")
    events = db.list_current_run_events(db_path, "asset-1")

    assert job is not None
    assert job.progress_total_chunks == 3
    assert job.progress_done_chunks == 1
    assert job.next_retry_at == 42
    assert [event.message for event in events] == ["transcribing", "Chunk retry"]
    assert events[-1].payload == {"chunk": 1}


def test_terminal_status_updates_assets_jobs_and_events(tmp_path: Path) -> None:
    db_path = tmp_path / "stt.sqlite3"
    db.initialize(db_path)
    create_asset(db_path, "failed")
    create_asset(db_path, "partial")
    create_asset(db_path, "success")
    for asset_id in ("failed", "partial", "success"):
        assert db.claim_next_job(db_path) == asset_id

    db.mark_failed(db_path, "failed", {"category": "provider", "message": "failed"})
    db.mark_partial(db_path, "partial", {"category": "provider", "message": "partial"})
    db.mark_success(
        db_path,
        "success",
        wav_path=tmp_path / "success.wav",
        duration=1.0,
        diarization_stats={},
        raw_segments=[],
        merged_segments=[],
        speaker_centroids={},
        transcript_segments=[],
        exports={},
    )

    for asset_id, status in (("failed", "failed"), ("partial", "partial"), ("success", "success")):
        asset = db.get_asset(db_path, asset_id)
        assert asset is not None
        assert asset["status"] == status
        assert asset["job"]["status"] == status
        assert asset["job"]["claim_owner"] is None
        assert asset["job"]["claim_expires_at"] is None
    success_job = db.get_job(db_path, "success")
    assert success_job is not None
    assert success_job.stage == "done"
    assert [event.message for event in db.list_events(db_path, "success")] == ["Job completed"]


def test_job_and_event_json_boundaries_reject_wrong_shapes(tmp_path: Path) -> None:
    db_path = tmp_path / "stt.sqlite3"
    db.initialize(db_path)
    create_asset(db_path)
    with db.transaction(db_path) as conn:
        conn.execute("UPDATE jobs SET error = ? WHERE asset_id = ?", ("[]", "asset-1"))

    with pytest.raises(ValueError, match="error must decode to dict"):
        db.get_job(db_path, "asset-1")

    with db.transaction(db_path) as conn:
        conn.execute("UPDATE jobs SET error = NULL WHERE asset_id = ?", ("asset-1",))
    db.add_event(db_path, "asset-1", "info", None, "event")
    with db.transaction(db_path) as conn:
        conn.execute("UPDATE job_events SET payload = ? WHERE asset_id = ?", ("[]", "asset-1"))

    with pytest.raises(ValueError, match="payload must decode to dict"):
        db.list_events(db_path, "asset-1")
