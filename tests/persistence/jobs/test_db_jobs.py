from pathlib import Path

import pytest
from _support.db_assets import initialized_db

from stt_vault.core.models.records import (
    ClaimNextJob,
    CompleteAsset,
    DiarizationMetadata,
    ErrorRecord,
    ExportPaths,
    JobEventCreate,
    JobProgressUpdate,
    NewAsset,
)


def create_asset(database, asset_id: str, tmp_path: Path) -> None:
    database.create_asset(NewAsset(asset_id, f"{asset_id}.wav", "audio", tmp_path / "clip.wav"))


@pytest.mark.parametrize("lease_seconds", [0, -1])
def test_queue_lease_validation_rejects_non_positive_values(
    tmp_path: Path, lease_seconds: int
) -> None:
    database = initialized_db(tmp_path)
    create_asset(database, "asset-1", tmp_path)

    with pytest.raises(ValueError, match="lease_seconds must be positive"):
        database.claim_next_job(ClaimNextJob("worker-a", lease_seconds))


def test_progress_and_current_run_events_are_persisted_in_creation_order(tmp_path: Path) -> None:
    database = initialized_db(tmp_path)
    create_asset(database, "asset-1", tmp_path)
    claim = database.claim_next_job(ClaimNextJob("worker-a", 60))
    assert claim is not None

    database.update_stage(asset_id="asset-1", stage="transcribing")
    database.update_progress(JobProgressUpdate("asset-1", 3, 1, next_retry_at=42))
    database.add_event(JobEventCreate("asset-1", "warning", "transcribing", "Chunk retry"))

    job = database.get_job("asset-1")
    events = database.list_current_run_events("asset-1")
    assert job is not None
    assert job.progress_total_chunks == 3
    assert job.progress_done_chunks == 1
    assert job.next_retry_at == 42
    assert [event.message for event in events] == ["transcribing", "Chunk retry"]
    assert events[-1].payload is None


def test_terminal_status_updates_assets_jobs_and_events(tmp_path: Path) -> None:
    database = initialized_db(tmp_path)
    for asset_id in ("failed", "partial", "success"):
        create_asset(database, asset_id, tmp_path)
        assert database.claim_next_job(ClaimNextJob("worker-a", 60)) is not None

    database.mark_failed("failed", ErrorRecord("provider", "failed"))
    database.mark_partial("partial", ErrorRecord("provider", "partial"))
    database.complete_asset(
        CompleteAsset(
            "success",
            DiarizationMetadata("success", tmp_path / "success.wav", 1.0, {}, [], [], {}),
            (),
            ExportPaths(),
        )
    )

    for asset_id, status in (("failed", "failed"), ("partial", "partial"), ("success", "success")):
        asset = database.get_asset(asset_id)
        job = database.get_job(asset_id)
        assert asset is not None
        assert job is not None
        assert asset.status == status
        assert job.status == status
        assert job.claim_owner is None
        assert job.claim_expires_at is None
