from pathlib import Path

import pytest
from _support.db_assets import initialized_db

from stt_vault.core.models.persistence_errors import AssetNotFoundError
from stt_vault.core.models.records import (
    AiSpeakerName,
    ApplyAiSpeakerNames,
    AssetCleanup,
    AssetSummaryUpdate,
    ClaimNextJob,
    CompleteAsset,
    DiarizationMetadata,
    ExportPaths,
    NewAsset,
    TranscriptChunkUpsert,
    TranscriptSegment,
)


def test_asset_lifecycle_methods_are_owned_by_one_database_instance(tmp_path: Path) -> None:
    database = initialized_db(tmp_path)
    database.create_asset(
        NewAsset("asset-1", "2026-07-15_12-57-52.mp4", "video", tmp_path / "clip.mp4")
    )
    metadata = DiarizationMetadata(
        "asset-1", tmp_path / "clip.wav", 12.5, {"segments": 1}, [], [], {"SPEAKER_00": [0.2]}
    )
    database.complete_asset(
        CompleteAsset(
            "asset-1",
            metadata,
            (),
            ExportPaths(),
        )
    )
    database.update_asset_exports("asset-1", ExportPaths(ai_text="/tmp/clip.txt"))
    database.update_asset_summary(
        AssetSummaryUpdate("asset-1", "success", text="Summary", model="test")
    )
    database.upsert_transcript_chunk(
        TranscriptChunkUpsert("asset-1", 0, TranscriptSegment(0.0, 1.0, "SPEAKER_00", "Hello"), 1)
    )
    applied = database.apply_speaker_name_updates(
        ApplyAiSpeakerNames("asset-1", (AiSpeakerName("SPEAKER_00", "Maya Chen"),))
    )
    assert applied.names == (AiSpeakerName("SPEAKER_00", "Maya Chen"),)

    asset = database.get_asset("asset-1")
    assert asset is not None
    assert asset.recorded_at == 1_784_120_272
    assert asset.diarization_stats == {"segments": 1}
    assert asset.exports.ai_text == "/tmp/clip.txt"
    assert asset.summary_text == "Summary"
    assert asset.transcript_segments[0].speaker_name == "Maya Chen"


def test_retry_and_cleanup_lifecycle_preserves_events_and_not_found_error(tmp_path: Path) -> None:
    database = initialized_db(tmp_path)
    database.create_asset(NewAsset("asset-1", "clip.wav", "audio", tmp_path / "clip.wav"))
    claim = database.claim_next_job(ClaimNextJob("test-worker", 60))
    assert claim is not None
    database.retry_asset("asset-1")
    assert database.list_events("asset-1")[-1].message == "Job queued for retry"

    cleanup = AssetCleanup("asset-1", tmp_path / "media", tmp_path / "exports")
    database.delete_asset_with_cleanup_task(cleanup)
    assert database.get_cleanup_task("asset-1") is not None
    with pytest.raises(AssetNotFoundError):
        database.delete_asset_with_cleanup_task(
            AssetCleanup("missing", tmp_path / "media", tmp_path / "exports")
        )
