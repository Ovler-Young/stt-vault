from dataclasses import replace
from pathlib import Path

import pytest

from stt_vault.core.models.records import (
    ClaimNextJob,
    CompleteDiarizationProviderInvocation,
    CompleteTranscriptionProviderInvocation,
    DiarizationMetadata,
    NewAsset,
    PrepareProviderWorkItem,
    ProviderInvocationTransition,
    TimedTranscriptUnit,
    TranscriptSegment,
)
from stt_vault.persistence.sqlite_database import SqliteDatabase


def test_transcription_completion_atomically_writes_the_chunk_and_provider_state(
    tmp_path: Path,
) -> None:
    database = SqliteDatabase(tmp_path / "atomic.sqlite3")
    database.initialize()
    database.create_asset(NewAsset("asset-1", "clip.wav", "audio", tmp_path / "clip.wav"))
    database.claim_next_job(ClaimNextJob("worker-1", 30, now=10))
    prepared = database.prepare_provider_work_item(
        PrepareProviderWorkItem.for_transcription(
            work_item_id="work-1",
            job_id="asset-1",
            asset_id="asset-1",
            chunk_key="chunk:0",
            run_attempt=1,
            idempotency_key="123e4567-e89b-42d3-a456-426614174000",
            request_hash="a" * 64,
        )
    )
    assert database.transition_provider_invocation(
        ProviderInvocationTransition.sent(prepared)
    ).applied
    assert database.transition_provider_invocation(
        ProviderInvocationTransition.accepted(prepared)
    ).applied

    command = CompleteTranscriptionProviderInvocation(
        work_item_id="work-1",
        invocation_attempt=1,
        claimant_run_attempt=1,
        asset_id="asset-1",
        chunk_index=0,
        segment=TranscriptSegment(0.0, 1.0, "SPEAKER_00", "hello"),
        attempts=1,
    )

    assert database.complete_transcription_and_provider_invocation(command).applied
    assert database.get_provider_invocation("work-1", 1).state == "completed"
    assert database.list_transcript_chunks("asset-1")[0].text == "hello"
    assert not database.complete_transcription_and_provider_invocation(command).applied
    assert len(database.list_transcript_chunks("asset-1")) == 1


def test_transcription_completion_replaces_timed_units_in_the_same_conditional_transaction(
    tmp_path: Path,
) -> None:
    """A stale completion cannot replace the completed chunk's timed-unit collection."""
    database = SqliteDatabase(tmp_path / "timed-units.sqlite3")
    database.initialize()
    database.create_asset(NewAsset("asset-1", "clip.wav", "audio", tmp_path / "clip.wav"))
    database.claim_next_job(ClaimNextJob("worker-1", 30, now=10))
    prepared = database.prepare_provider_work_item(
        PrepareProviderWorkItem.for_transcription(
            work_item_id="work-1",
            job_id="asset-1",
            asset_id="asset-1",
            chunk_key="chunk:0",
            run_attempt=1,
            idempotency_key="123e4567-e89b-42d3-a456-426614174000",
            request_hash="a" * 64,
        )
    )
    assert database.transition_provider_invocation(
        ProviderInvocationTransition.sent(prepared)
    ).applied
    assert database.transition_provider_invocation(
        ProviderInvocationTransition.accepted(prepared)
    ).applied

    command = CompleteTranscriptionProviderInvocation(
        work_item_id="work-1",
        invocation_attempt=1,
        claimant_run_attempt=1,
        asset_id="asset-1",
        chunk_index=0,
        segment=TranscriptSegment(0.0, 1.0, "SPEAKER_00", "hello"),
        attempts=1,
        timed_units=(
            TimedTranscriptUnit(0, "hel", 0, 500, 0.9, "en", "word"),
            TimedTranscriptUnit(1, "lo", 500, 1000, 0.8, "en", "word"),
        ),
    )

    assert database.complete_transcription_and_provider_invocation(command).applied
    assert [unit.unit_index for unit in database.list_transcript_timed_units("asset-1")] == [0, 1]
    assert not database.complete_transcription_and_provider_invocation(command).applied
    assert [unit.text for unit in database.list_transcript_timed_units("asset-1")] == ["hel", "lo"]


def test_diarization_completion_rolls_back_late_failure_and_retries_without_stale_writes(
    tmp_path: Path, monkeypatch
) -> None:
    database = SqliteDatabase(tmp_path / "diarization-atomic.sqlite3")
    database.initialize()
    database.create_asset(NewAsset("asset-1", "clip.wav", "audio", tmp_path / "clip.wav"))
    database.claim_next_job(ClaimNextJob("worker-1", 30, now=10))
    prepared = database.prepare_provider_work_item(
        PrepareProviderWorkItem(
            "work-1",
            "asset-1",
            "asset-1",
            "diarization",
            "asset",
            1,
            "123e4567-e89b-42d3-a456-426614174001",
            "b" * 64,
        )
    )
    database.transition_provider_invocation(ProviderInvocationTransition.sent(prepared))
    database.transition_provider_invocation(ProviderInvocationTransition.accepted(prepared))
    command = CompleteDiarizationProviderInvocation(
        "work-1",
        1,
        1,
        "asset-1",
        DiarizationMetadata(
            "asset-1",
            tmp_path / "clip.wav",
            1.0,
            {"segments": 1},
            [],
            [],
            {"SPEAKER_00": [0.1, 0.2]},
        ),
    )
    stale_command = replace(command, claimant_run_attempt=2)
    assert not database.complete_diarization_and_provider_invocation(stale_command).applied
    assert database.get_provider_invocation("work-1", 1).state == "accepted"
    assert database.get_asset("asset-1").duration is None

    def fail_late(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("forced audit failure")

    monkeypatch.setattr(database, "_append_provider_audit_event", fail_late)
    with pytest.raises(RuntimeError, match="forced audit failure"):
        database.complete_diarization_and_provider_invocation(command)
    assert database.get_provider_invocation("work-1", 1).state == "accepted"
    assert database.get_asset("asset-1").duration is None

    monkeypatch.undo()
    assert database.complete_diarization_and_provider_invocation(command).applied
    assert database.get_provider_invocation("work-1", 1).state == "completed"
    assert database.get_asset("asset-1").duration == 1.0
    assert not database.complete_diarization_and_provider_invocation(command).applied
