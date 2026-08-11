from pathlib import Path

from _support.db_assets import create_processing_asset, initialized_db

from stt_vault.core.models.records import (
    AssetCleanup,
    AssetSummaryUpdate,
    ClaimNextJob,
    NewAsset,
    RenewJobClaim,
    VisualEvent,
)


def test_visual_events_replace_rows(tmp_path: Path) -> None:
    database = create_processing_asset(tmp_path)
    database.replace_visual_events(
        "asset-1",
        [VisualEvent(1.25, 0.4), VisualEvent(3.5, 0.9, "scene_change")],
    )
    events = database.list_visual_events("asset-1")
    assert [event.event_index for event in events] == [0, 1]
    assert events[1].kind == "scene_change"


def test_cleanup_task_summary_and_deletion_are_persisted(tmp_path: Path) -> None:
    database = initialized_db(tmp_path)
    database.create_asset(NewAsset("asset-1", "clip.mp4", "video", tmp_path / "clip.mp4"))
    cleanup = AssetCleanup("asset-1", tmp_path / "media", tmp_path / "exports")
    database.record_cleanup_task(cleanup)
    task = database.get_cleanup_task("asset-1")
    assert task is not None
    assert task.asset_id == "asset-1"
    assert task.media_path == str(tmp_path / "media")
    assert task.exports_path == str(tmp_path / "exports")
    database.update_asset_summary(
        AssetSummaryUpdate("asset-1", "success", text="Summary", model="test-model")
    )
    asset = database.get_asset("asset-1")
    assert asset is not None
    assert asset.summary_text == "Summary"
    database.delete_asset_with_cleanup_task(cleanup)
    assert database.get_asset("asset-1") is None
    assert database.get_cleanup_task("asset-1") is not None


def test_job_claim_renewal_preserves_active_lease(tmp_path: Path) -> None:
    database = initialized_db(tmp_path)
    database.create_asset(NewAsset("asset-1", "clip.mp4", "video", tmp_path / "clip.mp4"))
    claim = database.claim_next_job(ClaimNextJob("worker-a", 60))
    assert claim is not None
    assert database.renew_job_claim(RenewJobClaim("asset-1", "worker-a", 60))
