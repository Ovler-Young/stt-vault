import json
import logging
import sys
from pathlib import Path

from stt_vault import db
from stt_vault.logging_config import StructuredFormatter, configure_logging, job_log_context


def test_structured_formatter_includes_standard_context_keys() -> None:
    formatter = StructuredFormatter()
    record = logging.makeLogRecord(
        {
            "name": "stt_vault.visual",
            "levelno": logging.ERROR,
            "levelname": "ERROR",
            "msg": "ffmpeg failed",
            "asset_id": "asset-1",
            "job_id": "job-1",
            "return_code": 1,
            "command": "ffmpeg",
            "stderr": "invalid media [truncated]",
        }
    )

    assert json.loads(formatter.format(record)) == {
        "event_name": "log.message",
        "level": "ERROR",
        "logger": "stt_vault.visual",
        "message": "ffmpeg failed",
        "asset_id": "asset-1",
        "job_id": "job-1",
        "return_code": 1,
        "command": "ffmpeg",
        "stderr": "invalid media [truncated]",
    }


def test_structured_formatter_preserves_stable_event_and_job_correlation() -> None:
    formatter = StructuredFormatter()
    record = logging.makeLogRecord(
        {
            "name": "stt_vault.worker",
            "levelno": logging.ERROR,
            "levelname": "ERROR",
            "msg": "worker job failed",
            "event_name": "worker.job_failed",
            "asset_id": "asset-1",
            "job_id": "job-7",
        }
    )

    event = json.loads(formatter.format(record))

    assert event["event_name"] == "worker.job_failed"
    assert event["asset_id"] == "asset-1"
    assert event["job_id"] == "job-7"


def test_job_log_context_uses_persisted_job_identifier(tmp_path: Path) -> None:
    db_path = tmp_path / "app.sqlite3"
    db.initialize(db_path)
    db.create_asset(db_path, "asset-1", "clip.wav", "audio", tmp_path / "clip.wav")

    context = job_log_context(db_path, "asset-1")
    event = json.loads(
        StructuredFormatter().format(
            logging.makeLogRecord(
                {
                    "name": "stt_vault.worker",
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
    assert event["job_id"] == db.get_job(db_path, "asset-1").id


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


def test_structured_formatter_redacts_dynamic_context_and_exception_details() -> None:
    formatter = StructuredFormatter()
    try:
        raise RuntimeError("OPENAI_API_KEY=secret-value /srv/private/clip.wav")
    except RuntimeError:
        record = logging.makeLogRecord(
            {
                "name": "stt_vault.worker",
                "levelno": logging.ERROR,
                "levelname": "ERROR",
                "msg": "worker failed",
                "cause": "Bearer token-value /srv/private/clip.wav",
                "exc_info": sys.exc_info(),
            }
        )

    rendered = formatter.format(record)
    assert "secret-value" not in rendered
    assert "token-value" not in rendered
    assert "/srv/private/clip.wav" not in rendered
    assert json.loads(rendered)["cause"] == "[redacted] [path]"


def test_structured_formatter_redacts_and_bounds_nested_context() -> None:
    formatter = StructuredFormatter()
    record = logging.makeLogRecord(
        {
            "name": "stt_vault.worker",
            "levelno": logging.ERROR,
            "levelname": "ERROR",
            "msg": "worker failed",
            "details": {
                "request": {
                    "authorization": "Bearer nested-secret",
                    "source": "/srv/private/clip.wav",
                },
                "levels": {"one": {"two": {"three": {"four": "hidden"}}}},
            },
        }
    )

    event = json.loads(formatter.format(record))

    assert "nested-secret" not in json.dumps(event)
    assert "/srv/private/clip.wav" not in json.dumps(event)
    assert event["details"]["request"] == {
        "authorization": "[redacted]",
        "source": "[path]",
    }
    assert event["details"]["levels"]["one"]["two"]["three"] == "[truncated]"
