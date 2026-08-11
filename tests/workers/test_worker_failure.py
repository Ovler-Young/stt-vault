import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from stt_vault.core.diagnostics.logging import StructuredFormatter
from stt_vault.core.models.records import ErrorRecord
from stt_vault.workers.worker_failure import WorkerFailureHandler, classify_worker_failure


def test_classify_worker_failure_returns_safe_persisted_errors() -> None:
    assert classify_worker_failure(OSError("/srv/private/clip.wav")) == ErrorRecord(
        "filesystem", "A local processing operation failed"
    )
    assert classify_worker_failure(RuntimeError("processing failed")) == ErrorRecord(
        "processing", "Asset processing failed"
    )


def test_worker_failure_handler_persists_category_and_logs_safe_diagnostics(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakeRepository:
        def __init__(self) -> None:
            self.failures: list[tuple[str, ErrorRecord]] = []

        def mark_failed(self, asset_id: str, error: ErrorRecord) -> None:
            self.failures.append((asset_id, error))

    monkeypatch.setattr(
        "stt_vault.workers.worker_failure.job_log_context",
        lambda _db_path, asset_id: {
            "asset_id": asset_id,
            "job_id": "job-1",
            "details": {
                "authorization": "Bearer nested-secret",
                "levels": {"one": {"two": {"three": {"four": "hidden"}}}},
            },
        },
    )
    repository = FakeRepository()
    handler = WorkerFailureHandler(
        SimpleNamespace(stt_db_path=tmp_path / "app.sqlite3"), repository
    )

    with caplog.at_level(logging.ERROR, logger="stt_vault.workers.worker_failure"):
        handler.handle("asset-1", OSError("failed at /srv/private/clip.wav token=secret-value"))

    assert repository.failures == [
        (
            "asset-1",
            ErrorRecord("filesystem", "A local processing operation failed"),
        )
    ]
    events = [json.loads(StructuredFormatter().format(record)) for record in caplog.records]
    assert [event["event_name"] for event in events] == [
        "worker.job_failed",
        "worker.failure_categorized",
    ]
    for record, event in zip(caplog.records, events, strict=True):
        rendered = json.dumps(event)
        assert record.exc_info is None
        assert "/srv/private/clip.wav" not in rendered
        assert "secret-value" not in rendered
        assert "nested-secret" not in rendered
        assert event["cause"] == "failed at [path] [redacted]"
        assert event["details"]["authorization"] == "[redacted]"
        assert event["details"]["levels"]["one"]["two"]["three"] == "[truncated]"
