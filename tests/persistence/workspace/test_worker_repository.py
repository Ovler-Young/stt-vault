from pathlib import Path
from types import SimpleNamespace

import pytest

from stt_vault.core.models.records import ErrorRecord, KnownSpeaker
from stt_vault.persistence.workspace.worker_repository import SqliteWorkerRepository
from stt_vault.workers.worker_completion import CompletionPersistence, SummaryFollowup
from stt_vault.workers.worker_exports import TranscriptExportStage, VisualEventStage
from stt_vault.workers.worker_media import DiarizationStage, MediaPreparationStage
from stt_vault.workers.worker_transcription import TranscriptionStage


def test_sqlite_worker_repository_delegates_operations_with_its_database_path(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from stt_vault.persistence.workspace import worker_repository

    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    class RecordingDatabase:
        def __getattr__(self, name: str):
            def operation(*args: object, **kwargs: object) -> object:
                calls.append((name, args, kwargs))
                return {
                    "claim_next_job": "asset-1",
                    "renew_job_claim": True,
                    "get_asset": {"id": "asset-1"},
                    "list_transcript_chunks": [{"chunk_index": 0}],
                    "list_speakers": [known_speaker],
                    "apply_ai_speaker_names": {"SPEAKER_00": "Maya"},
                }.get(name)

            return operation

    known_speaker: KnownSpeaker = {
        "id": "speaker-1",
        "display_name": "Maya",
        "centroid": [0.1, 0.2],
        "sample_count": 3,
        "created_at": 1,
        "updated_at": 2,
    }
    database = RecordingDatabase()
    monkeypatch.setattr(worker_repository, "db", database)
    db_path = tmp_path / "app.sqlite3"
    repository = SqliteWorkerRepository(db_path)
    error: ErrorRecord = {"category": "processing", "message": "failed"}

    assert repository.claim_next_job("worker-1", 30) == "asset-1"
    assert repository.renew_job_claim("asset-1", "worker-1", 30)
    repository.mark_failed("asset-1", error)
    assert repository.get_asset("asset-1") == {"id": "asset-1"}
    assert repository.list_transcript_chunks("asset-1") == [{"chunk_index": 0}]
    repository.update_stage("asset-1", "transcribing speech")
    repository.update_diarization_metadata(
        "asset-1",
        wav_path=tmp_path / "audio.wav",
        duration=1.0,
        diarization_stats={},
        raw_segments=[],
        merged_segments=[],
        speaker_centroids={},
    )
    repository.reset_transcript_chunks("asset-1")
    repository.upsert_transcript_chunk("asset-1", 0, {"chunk_index": 0}, attempts=1)
    assert repository.list_speakers() == [known_speaker]
    repository.add_event("asset-1", "info", "stage", "message", {"value": "ok"})
    repository.update_progress("asset-1", done_chunks=1)
    with pytest.raises(TypeError):
        repository.update_progress("asset-1", unsupported=1)
    repository.mark_success(
        "asset-1",
        wav_path=tmp_path / "audio.wav",
        duration=1.0,
        diarization_stats={},
        raw_segments=[],
        merged_segments=[],
        speaker_centroids={},
        transcript_segments=[],
        exports={},
    )
    repository.mark_partial("asset-1", error)
    repository.replace_visual_events("asset-1", [])
    repository.update_asset_summary("asset-1", status="running")
    assert repository.apply_ai_speaker_names("asset-1", {"SPEAKER_00": "Maya"}) == {
        "SPEAKER_00": "Maya"
    }

    assert [name for name, _args, _kwargs in calls] == [
        "claim_next_job",
        "renew_job_claim",
        "mark_failed",
        "get_asset",
        "list_transcript_chunks",
        "update_stage",
        "update_diarization_metadata",
        "reset_transcript_chunks",
        "upsert_transcript_chunk",
        "list_speakers",
        "add_event",
        "update_progress",
        "mark_success",
        "mark_partial",
        "replace_visual_events",
        "update_asset_summary",
        "apply_ai_speaker_names",
    ]
    assert all(args[0] == db_path for _name, args, _kwargs in calls)
    get_asset_call = next((args, kwargs) for name, args, kwargs in calls if name == "get_asset")
    assert get_asset_call == ((db_path, "asset-1"), {"include_event_history": False})


def test_worker_components_keep_explicit_repository_injection(tmp_path: Path) -> None:
    settings = SimpleNamespace(stt_db_path=tmp_path / "app.sqlite3")
    repository = object()

    assert MediaPreparationStage(settings, repository=repository).repository is repository
    assert (
        DiarizationStage(settings, SimpleNamespace(), repository=repository).repository
        is repository
    )
    assert TranscriptExportStage(settings, repository=repository).repository is repository
    assert VisualEventStage(settings, repository=repository).repository is repository
    assert CompletionPersistence(settings, repository=repository).repository is repository
    assert SummaryFollowup(settings, repository=repository).repository is repository

    transcription = TranscriptionStage(settings, repository=repository)

    assert transcription.chunk_persistence.repository is repository
    assert transcription.speaker_reconciler.repository is repository
    assert transcription.progress_events.repository is repository
