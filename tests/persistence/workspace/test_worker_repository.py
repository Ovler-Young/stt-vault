from pathlib import Path
from types import SimpleNamespace

from _support.db_assets import initialized_db

from stt_vault.workers.worker_completion import CompletionPersistence, SummaryFollowup
from stt_vault.workers.worker_exports import TranscriptExportStage, VisualEventStage
from stt_vault.workers.worker_media import DiarizationStage, MediaPreparationStage
from stt_vault.workers.worker_transcription import TranscriptionStage


def test_worker_components_keep_explicit_database_injection(tmp_path: Path) -> None:
    settings = SimpleNamespace(stt_db_path=tmp_path / "app.sqlite3")
    database = initialized_db(tmp_path)

    assert MediaPreparationStage(settings, database=database).database is database
    assert DiarizationStage(settings, SimpleNamespace(), database=database).database is database
    assert TranscriptExportStage(settings, database=database).database is database
    assert VisualEventStage(settings, database=database).database is database
    assert CompletionPersistence(settings, database=database).database is database
    assert SummaryFollowup(settings, database=database).database is database

    transcription = TranscriptionStage(settings, database=database)
    assert transcription.chunk_persistence.database is database
    assert transcription.speaker_reconciler.database is database
    assert transcription.progress_events.database is database
