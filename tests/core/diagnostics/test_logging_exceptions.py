import json
import logging

import pytest

from stt_vault.core.diagnostics.logging import StructuredFormatter, log_exception_diagnostic


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
