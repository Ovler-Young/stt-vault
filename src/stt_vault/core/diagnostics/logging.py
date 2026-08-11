import json
import logging
import math
from collections.abc import Mapping, Sequence
from itertools import islice

from stt_vault.persistence.sqlite_database import SqliteDatabase

from .process import format_diagnostic_text

_STANDARD_LOG_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__)
_RESERVED_EXTRA_FIELDS = _STANDARD_LOG_RECORD_FIELDS | {"asctime", "message"}
_DIAGNOSTIC_FIELDS = frozenset({"event_name", "cause", "error_type"})


def job_log_context(database: SqliteDatabase, asset_id: str) -> dict[str, str | None]:
    """Correlate a worker log record with its persisted job when available."""
    job = database.get_job(asset_id)
    return {"asset_id": asset_id, "job_id": job.job_id if job is not None else None}


def log_exception_diagnostic(
    logger: logging.Logger,
    message: str,
    error: Exception,
    *,
    event_name: str,
    context: Mapping[str, object] | None = None,
) -> None:
    """Log a bounded, redacted exception summary without its traceback."""
    logger.error(
        message,
        extra={
            **_diagnostic_context(context),
            "event_name": event_name,
            "cause": format_diagnostic_text(str(error)),
            "error_type": error.__class__.__name__,
        },
    )


def _diagnostic_context(context: Mapping[str, object] | None) -> dict[str, object]:
    """Exclude log-record and diagnostic fields that caller context cannot own."""
    if context is None:
        return {}
    return {
        key: value
        for key, value in context.items()
        if key not in _RESERVED_EXTRA_FIELDS and key not in _DIAGNOSTIC_FIELDS
    }


class StructuredFormatter(logging.Formatter):
    """Emit application logs as JSON to the standard error sink."""

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        event = {
            "event_name": _format_optional_context(getattr(record, "event_name", "log.message")),
            "level": record.levelname,
            "logger": record.name,
            "message": format_diagnostic_text(record.message),
            "asset_id": _format_optional_context(getattr(record, "asset_id", None)),
            "job_id": _format_optional_context(getattr(record, "job_id", None)),
            "return_code": _format_optional_context(getattr(record, "return_code", None)),
            "command": _format_optional_context(getattr(record, "command", None)),
            "stderr": _format_optional_context(getattr(record, "stderr", None)),
        }
        if record.exc_info:
            event["exception"] = format_diagnostic_text(self.formatException(record.exc_info))
        for key, value in record.__dict__.items():
            if key not in _STANDARD_LOG_RECORD_FIELDS and key not in event:
                event[key] = _format_optional_context(value)
        return json.dumps(event, default=str, allow_nan=False)


MAX_CONTEXT_DEPTH = 4
MAX_CONTEXT_ITEMS = 50


def _format_optional_context(value: object, *, depth: int = 0) -> object:
    if depth >= MAX_CONTEXT_DEPTH:
        return "[truncated]"
    if isinstance(value, str):
        return format_diagnostic_text(value)
    if isinstance(value, Mapping):
        items = list(islice(value.items(), MAX_CONTEXT_ITEMS + 1))
        formatted: dict[str, object] = {}
        for key, item in items[:MAX_CONTEXT_ITEMS]:
            safe_key = format_diagnostic_text(str(key))
            if safe_key in formatted:
                suffix = 2
                while f"{safe_key}#{suffix}" in formatted:
                    suffix += 1
                safe_key = f"{safe_key}#{suffix}"
            formatted[safe_key] = _format_optional_context(item, depth=depth + 1)
        if len(items) == MAX_CONTEXT_ITEMS + 1:
            formatted["_truncated"] = "[truncated]"
        return formatted
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        formatted = [
            _format_optional_context(item, depth=depth + 1)
            for item in islice(value, MAX_CONTEXT_ITEMS)
        ]
        if len(value) > MAX_CONTEXT_ITEMS:
            formatted.append("[truncated]")
        return formatted
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return format_diagnostic_text(str(value))


def configure_logging() -> None:
    root = logging.getLogger()
    formatter = StructuredFormatter()
    root.setLevel(logging.INFO)
    if not root.handlers:
        root.addHandler(logging.StreamHandler())
    for handler in root.handlers:
        handler.setFormatter(formatter)
