import json
import logging
import sys
from pathlib import Path

import pytest

from stt_vault.core.diagnostics.logging import (
    StructuredFormatter,
    configure_logging,
    job_log_context,
    log_exception_diagnostic,
)
from stt_vault.persistence import db


def test_structured_formatter_includes_standard_context_keys() -> None:
    formatter = StructuredFormatter()
    record = logging.makeLogRecord(
        {
            "name": "stt_vault.processing.visual",
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
        "logger": "stt_vault.processing.visual",
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
            "name": "stt_vault.workers.worker",
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
                "name": "stt_vault.workers.worker",
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
            "name": "stt_vault.workers.worker",
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


def test_exception_diagnostic_logs_redacted_cause_without_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("stt_vault.tests.diagnostics")
    error = RuntimeError("failed at /srv/private/clip.wav with token=secret-value")

    with caplog.at_level(logging.ERROR, logger=logger.name):
        log_exception_diagnostic(
            logger,
            "operation failed",
            error,
            event_name="test.operation_failed",
            context={"source_path": "/srv/private/clip.wav"},
        )

    record = caplog.records[-1]
    rendered = StructuredFormatter().format(record)
    event = json.loads(rendered)
    assert record.exc_info is None
    assert "/srv/private/clip.wav" not in rendered
    assert "secret-value" not in rendered
    assert event["event_name"] == "test.operation_failed"
    assert event["cause"] == "failed at [path] with [redacted]"


def test_exception_diagnostic_reserved_fields_override_context(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("stt_vault.tests.diagnostics.reserved")
    error = RuntimeError("failed at /srv/private/clip.wav with token=secret-value")

    with caplog.at_level(logging.ERROR, logger=logger.name):
        log_exception_diagnostic(
            logger,
            "operation failed",
            error,
            event_name="test.operation_failed",
            context={
                "event_name": "attacker.event",
                "cause": "/srv/private/raw secret-value",
                "error_type": "spoofed",
            },
        )

    event = json.loads(StructuredFormatter().format(caplog.records[-1]))
    assert event["event_name"] == "test.operation_failed"
    assert event["cause"] == "failed at [path] with [redacted]"
    assert event["error_type"] == "RuntimeError"


def test_exception_diagnostic_filters_log_record_field_collisions(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("stt_vault.tests.diagnostics.collisions")

    with caplog.at_level(logging.ERROR, logger=logger.name):
        log_exception_diagnostic(
            logger,
            "operation failed",
            RuntimeError("failed"),
            event_name="test.operation_failed",
            context={
                "msg": "spoofed message",
                "name": "spoofed.logger",
                "levelno": logging.DEBUG,
                "exc_info": "spoofed traceback",
                "request_id": "request-1",
            },
        )

    record = caplog.records[-1]
    event = json.loads(StructuredFormatter().format(record))
    assert record.getMessage() == "operation failed"
    assert record.name == logger.name
    assert record.levelno == logging.ERROR
    assert record.exc_info is None
    assert event["event_name"] == "test.operation_failed"
    assert event["request_id"] == "request-1"
