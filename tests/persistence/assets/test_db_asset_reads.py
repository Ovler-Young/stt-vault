import sqlite3
from pathlib import Path
from typing import Any

import pytest
from _support.db_assets import create_processing_asset, initialized_db

from stt_vault.core.models.api import EventResponse, JobResponse
from stt_vault.persistence import db


def chunk(
    start: float,
    end: float,
    speaker: str,
    text: str,
    **overrides: Any,
) -> dict[str, Any]:
    data = {
        "start": start,
        "end": end,
        "speaker": speaker,
        "text": text,
    }
    data.update(overrides)
    return data


def test_asset_job_lifecycle_and_get_asset_aggregate(tmp_path: Path) -> None:
    db_path = initialized_db(tmp_path)
    db.create_asset(db_path, "asset-1", "clip.mp4", "video", tmp_path / "clip.mp4")

    [listed_asset] = db.list_assets(db_path)
    [listed_job] = db.list_jobs(db_path)
    assert listed_asset["status"] == "queued"
    assert listed_asset["filename"] == "clip.mp4"
    assert listed_job.asset_id == "asset-1"
    assert listed_job.filename == "clip.mp4"
    assert isinstance(listed_job, JobResponse)

    assert db.claim_next_job(db_path) == "asset-1"
    db.update_stage(db_path, "asset-1", "transcribing speech")
    db.update_progress(
        db_path,
        "asset-1",
        total_chunks=3,
        done_chunks=1,
        failed_chunks=1,
        next_retry_at=12345,
    )
    db.add_event(
        db_path,
        "asset-1",
        "warning",
        "transcribing speech",
        "Chunk retry scheduled",
        {"chunk_index": 1},
    )
    event = db.list_events(db_path, "asset-1")[-1]
    assert isinstance(event, EventResponse)
    assert event.message == "Chunk retry scheduled"

    asset = db.get_asset(db_path, "asset-1")

    assert asset is not None
    assert asset["status"] == "processing"
    assert asset["job"]["stage"] == "transcribing speech"
    assert asset["job"]["run_attempt"] == 1
    assert asset["job"]["progress_total_chunks"] == 3
    assert asset["job"]["progress_done_chunks"] == 1
    assert asset["job"]["progress_failed_chunks"] == 1
    assert asset["job"]["next_retry_at"] == 12345
    assert [event["message"] for event in asset["events"]] == [
        "transcribing speech",
        "Chunk retry scheduled",
    ]
    assert asset["event_history"] == asset["events"]
    assert asset["events"][1]["payload"] == {"chunk_index": 1}


def test_get_asset_does_not_load_full_event_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = create_processing_asset(tmp_path)
    db.add_event(db_path, "asset-1", "info", "transcribing", "progress")

    def fail_if_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("get_asset must not load event history")

    monkeypatch.setattr(
        "stt_vault.persistence.assets.db_asset_records.list_events", fail_if_called, raising=False
    )

    asset = db.get_asset(db_path, "asset-1", include_event_history=False)

    assert asset is not None
    assert "event_history" not in asset


def test_transcript_chunks_are_ordered_decoded_and_mirrored(tmp_path: Path) -> None:
    db_path = create_processing_asset(tmp_path)

    db.upsert_transcript_chunk(
        db_path,
        "asset-1",
        1,
        chunk(10.0, 12.0, "SPEAKER_01", "second", chunk_start=9.5, chunk_end=12.5),
        attempts=1,
    )
    db.upsert_transcript_chunk(
        db_path,
        "asset-1",
        0,
        chunk(0.0, 2.0, "SPEAKER_00", "first"),
        attempts=2,
        status="failed",
        error={"message": "rate limited"},
    )

    chunks = db.list_transcript_chunks(db_path, "asset-1")
    asset = db.get_asset(db_path, "asset-1")

    assert [item["chunk_index"] for item in chunks] == [0, 1]
    assert chunks[0]["chunk_start"] == 0.0
    assert chunks[0]["chunk_end"] == 2.0
    assert chunks[0]["error"] == {"message": "rate limited"}
    assert chunks[1]["chunk_start"] == 9.5
    assert chunks[1]["chunk_end"] == 12.5
    assert asset is not None
    assert asset["transcript_segments"] == chunks

    db.reset_transcript_chunks(db_path, "asset-1")

    assert db.list_transcript_chunks(db_path, "asset-1") == []


def test_transcript_chunk_boundary_rejects_malformed_database_record(tmp_path: Path) -> None:
    db_path = create_processing_asset(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO transcript_chunks
                (asset_id, chunk_index, start, end, chunk_start, chunk_end,
                 speaker, text, status, attempts, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("asset-1", 0, "not-a-time", 1.0, 0.0, 1.0, "SPEAKER_00", "hello", "success", 1, 0),
        )

    with pytest.raises(ValueError, match="invalid shape"):
        db.list_transcript_chunks(db_path, "asset-1")
