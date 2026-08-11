from pathlib import Path

from _support.db_assets import create_processing_asset, initialized_db

from stt_vault.core.models.records import (
    ClaimNextJob,
    ErrorRecord,
    JobEventCreate,
    JobProgressUpdate,
    NewAsset,
    TranscriptChunkUpsert,
    TranscriptSegment,
)


def test_asset_job_lifecycle_uses_typed_records(tmp_path: Path) -> None:
    database = initialized_db(tmp_path)
    database.create_asset(NewAsset("asset-1", "clip.mp4", "video", tmp_path / "clip.mp4"))

    [asset] = database.list_assets()
    [job] = database.list_jobs()
    assert asset.status == "queued"
    assert job.asset_id == "asset-1"

    claim = database.claim_next_job(ClaimNextJob("test-worker", 60))
    assert claim is not None
    database.update_stage(asset_id="asset-1", stage="transcribing speech")
    database.update_progress(JobProgressUpdate("asset-1", 3, 1, 1, 12345))
    database.add_event(
        JobEventCreate("asset-1", "warning", "transcribing speech", "Chunk retry scheduled")
    )

    job = database.get_job("asset-1")
    assert job is not None
    assert job.stage == "transcribing speech"
    assert job.run_attempt == 1
    assert job.progress_total_chunks == 3
    assert job.progress_done_chunks == 1
    assert database.list_events("asset-1")[-1].message == "Chunk retry scheduled"


def test_transcript_chunks_are_ordered_and_reset(tmp_path: Path) -> None:
    database = create_processing_asset(tmp_path)
    database.upsert_transcript_chunk(
        TranscriptChunkUpsert(
            "asset-1",
            1,
            TranscriptSegment(10.0, 12.0, "SPEAKER_01", "second", chunk_start=9.5, chunk_end=12.5),
            1,
        )
    )
    database.upsert_transcript_chunk(
        TranscriptChunkUpsert(
            "asset-1",
            0,
            TranscriptSegment(0.0, 2.0, "SPEAKER_00", "first"),
            2,
            "failed",
            ErrorRecord("provider", "rate limited"),
        )
    )

    chunks = database.list_transcript_chunks("asset-1")
    assert [item.chunk_index for item in chunks] == [0, 1]
    assert chunks[0].chunk_start == 0.0
    assert chunks[1].chunk_end == 12.5

    database.reset_transcript_chunks("asset-1")
    assert database.list_transcript_chunks("asset-1") == []
