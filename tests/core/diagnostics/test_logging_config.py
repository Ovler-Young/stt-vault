import json
import logging
from pathlib import Path

from stt_vault.core.diagnostics.logging import (
    StructuredFormatter,
    configure_logging,
    job_log_context,
)
from stt_vault.core.models.records import NewAsset
from stt_vault.persistence.sqlite_database import SqliteDatabase


def test_job_log_context_uses_persisted_job_identifier(tmp_path: Path) -> None:
    db_path = tmp_path / "app.sqlite3"
    database = SqliteDatabase(db_path)
    database.initialize()
    database.create_asset(NewAsset("asset-1", "clip.wav", "audio", tmp_path / "clip.wav"))

    context = job_log_context(database, "asset-1")
    event = json.loads(
        StructuredFormatter().format(
            logging.makeLogRecord(
                {
                    "name": "stt_vault.workers.worker",
                    "levelno": logging.ERROR,
                    "levelname": "ERROR",
                    "msg": "worker job failed",
                    "event_name": "worker.job_failed",
                    **context,
                }
            )
        )
    )

    assert event["asset_id"] == "asset-1"
    job = database.get_job("asset-1")
    assert job is not None
    assert event["job_id"] == job.job_id


def test_configure_logging_reformats_existing_root_handlers() -> None:
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level
    existing_handler = logging.StreamHandler()
    root.handlers = [existing_handler]
    try:
        configure_logging()
        assert isinstance(existing_handler.formatter, StructuredFormatter)
        assert root.level == logging.INFO
    finally:
        root.handlers = original_handlers
        root.setLevel(original_level)
