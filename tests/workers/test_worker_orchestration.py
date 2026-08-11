import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from stt_vault.core.models.records import (
    AssetRecord,
    ExportPaths,
    ProviderRecoveryCommand,
    RecoveryProviderEntry,
    RecoveryProviderOutcome,
    TranscriptSegment,
)
from stt_vault.workers.worker import Worker
from stt_vault.workers.worker_models import PreparedAsset


def test_worker_process_asset_orchestrates_stage_services(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []
    prepared = PreparedAsset(
        wav_path=tmp_path / "audio.wav",
        duration=12.0,
        diarization_stats={},
        raw_segments=[],
        merged_segments=[],
        speaker_centroids={},
    )
    worker = Worker.__new__(Worker)
    worker.settings = SimpleNamespace(
        stt_db_path=tmp_path / "app.sqlite3", tmp_dir=tmp_path / "tmp"
    )
    worker.media_preparation = SimpleNamespace(
        prepare=lambda asset_id, asset: (
            calls.append("prepare"),
            (prepared.wav_path, prepared.duration),
        )[1]
    )
    worker.diarization = SimpleNamespace(
        diarize=lambda asset_id, wav_path, duration, **_kwargs: prepared
    )
    worker.transcription = SimpleNamespace(
        transcribe=lambda asset_id, asset, stage_prepared, work_dir: (
            calls.append("transcribe"),
            ([TranscriptSegment(0.0, 1.0, "SPEAKER_00", "hello")], None),
        )[1]
    )
    worker.transcript_exports = SimpleNamespace(
        write=lambda asset_id, asset, stage_prepared, segments, **kwargs: (
            calls.append(f"exports:{kwargs['partial']}"),
            ExportPaths(json="transcript.json"),
        )[1]
    )
    worker.visual_events = SimpleNamespace(detect=lambda _asset_id, _asset: ExportPaths())
    worker.completion = SimpleNamespace(
        complete=lambda asset_id, stage_prepared, segments, exports: calls.append("complete")
    )
    worker.database = SimpleNamespace(
        get_asset=lambda _asset_id: AssetRecord(
            "asset-1", "clip.wav", "audio", str(tmp_path / "clip.wav"), "processing", 1, 1
        ),
        list_transcript_chunks=lambda _asset_id: [],
    )

    worker.process_asset("asset-1")

    assert calls == ["prepare", "transcribe", "exports:False", "complete"]
    assert not (tmp_path / "tmp" / "asset-1").exists()


def test_worker_uses_named_injected_stage_factories() -> None:
    diarizer = SimpleNamespace()
    media_stage = SimpleNamespace()
    diarization_stage = SimpleNamespace()
    transcription_stage = SimpleNamespace()
    visual_stage = SimpleNamespace()
    completion_stage = SimpleNamespace()
    calls: list[str] = []

    worker = Worker(
        SimpleNamespace(),
        diarizer_factory=lambda _settings: (calls.append("diarizer"), diarizer)[1],
        media_preparation_stage_factory=lambda _settings, _database: (
            calls.append("media"),
            media_stage,
        )[1],
        diarization_stage_factory=lambda _settings, value, _database: (
            calls.append("diarization"),
            diarization_stage if value is diarizer else None,
        )[1],
        transcription_stage_factory=lambda _settings, _database: (
            calls.append("transcription"),
            transcription_stage,
        )[1],
        visual_event_stage_factory=lambda _settings, _database: (
            calls.append("visual"),
            visual_stage,
        )[1],
        transcript_export_stage_factory=lambda _settings, _database: (
            calls.append("exports"),
            SimpleNamespace(),
        )[1],
        completion_stage_factory=lambda _settings, _database: (
            calls.append("completion"),
            completion_stage,
        )[1],
        database=SimpleNamespace(),
    )

    assert calls == [
        "diarizer",
        "media",
        "diarization",
        "visual",
        "transcription",
        "exports",
        "completion",
    ]
    assert worker.diarizer is diarizer
    assert worker.media_preparation is media_stage
    assert worker.diarization is diarization_stage
    assert worker.transcription is transcription_stage
    assert worker.completion is completion_stage


@pytest.mark.parametrize("transcription_provider", ("openai", "mod-whisper-cpu"))
@pytest.mark.parametrize("state", ("sent", "accepted"))
def test_startup_recovery_abandons_local_senko_diarization_without_remote_cancellation(
    monkeypatch, transcription_provider: str, state: str
) -> None:
    """Local diarization entries have a deterministic cancellation-free recovery outcome."""
    entry = RecoveryProviderEntry(
        work_item_id="diarization-work",
        invocation_attempt=1,
        prior_run_attempt=4,
        expected_state=state,
        idempotency_key="00000000-0000-4000-8000-000000000001",
        role="diarization",
        provider_id="senko",
    )
    command = ProviderRecoveryCommand(
        job_id="job-1",
        asset_id="asset-1",
        prior_run_attempt=4,
        phase="diarizing",
        token="recovery-token",
        entries=(entry,),
    )
    completions = []
    worker = Worker.__new__(Worker)
    worker.settings = SimpleNamespace(stt_transcription_provider=transcription_provider)
    worker.database = SimpleNamespace(
        claim_recoverable_jobs=lambda _command: SimpleNamespace(commands=(command,)),
        complete_provider_recovery=completions.append,
    )

    class UnexpectedSidecarClient:
        def __init__(self) -> None:
            raise AssertionError("local Senko recovery must not construct a transcription client")

    monkeypatch.setattr(
        "stt_vault.workers.worker.SidecarTranscriptionClient", UnexpectedSidecarClient
    )

    worker.recover_startup_jobs()

    assert len(completions) == 1
    assert completions[0].command is command
    assert completions[0].outcomes == (RecoveryProviderOutcome.abandoned(entry),)


def test_startup_recovery_keeps_mixed_local_and_remote_outcomes_in_claim_order(monkeypatch) -> None:
    """Only the remote entry reaches its selected provider cancellation endpoint."""
    local_entry = RecoveryProviderEntry(
        work_item_id="diarization-work",
        invocation_attempt=1,
        prior_run_attempt=4,
        expected_state="accepted",
        idempotency_key="00000000-0000-4000-8000-000000000001",
        role="diarization",
        provider_id="senko",
    )
    remote_entry = RecoveryProviderEntry(
        work_item_id="transcription-work",
        invocation_attempt=2,
        prior_run_attempt=4,
        expected_state="sent",
        idempotency_key="00000000-0000-4000-8000-000000000002",
        role="transcription",
        provider_id="mod-whisper-cpu",
    )
    command = ProviderRecoveryCommand(
        job_id="job-1",
        asset_id="asset-1",
        prior_run_attempt=4,
        phase="transcribing speech",
        token="recovery-token",
        entries=(local_entry, remote_entry),
    )
    completions = []
    cancellation_keys: list[str] = []
    worker = Worker.__new__(Worker)
    worker.settings = SimpleNamespace(stt_transcription_provider="mod-whisper-cpu")
    worker.database = SimpleNamespace(
        claim_recoverable_jobs=lambda _command: SimpleNamespace(commands=(command,)),
        complete_provider_recovery=completions.append,
    )

    class SidecarClient:
        def cancel(self, idempotency_key: str) -> int:
            cancellation_keys.append(idempotency_key)
            return 204

    monkeypatch.setattr("stt_vault.workers.worker.SidecarTranscriptionClient", SidecarClient)

    worker.recover_startup_jobs()

    assert cancellation_keys == [remote_entry.idempotency_key]
    assert completions[0].outcomes == (
        RecoveryProviderOutcome.abandoned(local_entry),
        RecoveryProviderOutcome.cancelled(remote_entry, http_status=204),
    )


def test_recovered_job_uses_its_next_run_attempt_as_provider_work_generation() -> None:
    """A requeued local attempt must not reuse the interrupted work-item identity."""
    from stt_vault.core.models.records import JobClaim

    processed = []
    worker = Worker.__new__(Worker)
    worker.stop_event = threading.Event()
    worker.claim_owner = "worker-a"
    worker.settings = SimpleNamespace(job_lease_seconds=30)
    worker.diarizer = SimpleNamespace()
    worker.database = SimpleNamespace(
        claim_next_job=lambda _command: JobClaim("asset-1", "job-1", 2, 40),
        get_active_job_context=lambda _asset_id: SimpleNamespace(job_id="job-1", run_attempt=2),
    )
    worker.process_asset = lambda asset_id, context: (
        processed.append((asset_id, context)),
        worker.stop_event.set(),
    )

    worker.run()

    assert processed[0][0] == "asset-1"
    assert processed[0][1].run_attempt == 2
    assert processed[0][1].work_generation == 2
