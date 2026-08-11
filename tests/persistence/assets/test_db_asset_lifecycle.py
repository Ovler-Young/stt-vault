from pathlib import Path

from _support.db_assets import create_processing_asset

from stt_vault.core.models.records import (
    CompleteAsset,
    DiarizationMetadata,
    ErrorRecord,
    ExportPaths,
    JobProgressUpdate,
    TranscriptSegment,
)


def test_success_failure_partial_and_retry_transitions(tmp_path: Path) -> None:
    success_database = create_processing_asset(tmp_path / "success", "success-asset")
    success_database.complete_asset(
        CompleteAsset(
            "success-asset",
            DiarizationMetadata(
                "success-asset", tmp_path / "success.wav", 12.5, {"segments": 2}, [], [], {}
            ),
            (TranscriptSegment(0.0, 1.0, "SPEAKER_00", "hello"),),
            ExportPaths(ai_text="/tmp/success.txt"),
        )
    )
    success_asset = success_database.get_asset("success-asset")
    success_job = success_database.get_job("success-asset")
    assert success_asset is not None
    assert success_job is not None
    assert success_asset.status == "success"
    assert success_job.status == "success"
    assert success_job.stage == "done"
    assert success_asset.exports.ai_text == "/tmp/success.txt"

    failed_database = create_processing_asset(tmp_path / "failed", "failed-asset")
    failed_database.update_progress(JobProgressUpdate("failed-asset", 4, 2, 1))
    failed_database.mark_failed(
        "failed-asset", ErrorRecord("processing", "boom OPENAI_API_KEY=secret")
    )
    failed_asset = failed_database.get_asset("failed-asset")
    assert failed_asset is not None
    assert failed_asset.status == "failed"
    assert failed_asset.error == ErrorRecord("processing", "boom OPENAI_API_KEY=secret")

    failed_database.retry_asset("failed-asset")
    retried_job = failed_database.get_job("failed-asset")
    assert retried_job is not None
    assert retried_job.status == "queued"
    assert retried_job.error is None
    assert retried_job.progress_total_chunks == 0
    assert failed_database.list_events("failed-asset")[-1].run_attempt == 2

    partial_database = create_processing_asset(tmp_path / "partial", "partial-asset")
    partial_database.mark_partial("partial-asset", ErrorRecord("provider", "slow"))
    partial_job = partial_database.get_job("partial-asset")
    assert partial_job is not None
    assert partial_job.status == "partial"
    assert partial_job.error == ErrorRecord("provider", "slow")
