from pathlib import Path

from _db_asset_support import create_processing_asset

from stt_vault.persistence import db


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
