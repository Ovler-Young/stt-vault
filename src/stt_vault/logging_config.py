import json
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path

from .process_diagnostics import format_diagnostic_text


def job_log_context(db_path: Path, asset_id: str) -> dict[str, str | None]:
    """Correlate a worker log record with its persisted job when available."""
    from . import db

    job = db.get_job(db_path, asset_id)
    return {"asset_id": asset_id, "job_id": job["id"] if job is not None else None}


class StructuredFormatter(logging.Formatter):
    """Emit application logs as JSON to the standard error sink."""

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        event = {
            "event_name": getattr(record, "event_name", "log.message"),
            "level": record.levelname,
            "logger": record.name,
            "message": format_diagnostic_text(record.message),
            "asset_id": getattr(record, "asset_id", None),
            "job_id": getattr(record, "job_id", None),
            "return_code": getattr(record, "return_code", None),
            "command": getattr(record, "command", None),
            "stderr": _format_optional_context(getattr(record, "stderr", None)),
        }
        if record.exc_info:
            event["exception"] = format_diagnostic_text(self.formatException(record.exc_info))
        for key, value in record.__dict__.items():
            if key not in _STANDARD_LOG_RECORD_FIELDS and key not in event:
                event[key] = _format_optional_context(value)
        return json.dumps(event, default=str)


_STANDARD_LOG_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__)
MAX_CONTEXT_DEPTH = 4
MAX_CONTEXT_ITEMS = 50


def _format_optional_context(value: object, *, depth: int = 0) -> object:
    if depth >= MAX_CONTEXT_DEPTH:
        return "[truncated]"
    if isinstance(value, str):
        return format_diagnostic_text(value)
    if isinstance(value, Mapping):
        items = list(value.items())
        formatted = {
            str(key): _format_optional_context(item, depth=depth + 1)
            for key, item in items[:MAX_CONTEXT_ITEMS]
        }
        if len(items) > MAX_CONTEXT_ITEMS:
            formatted["_truncated"] = "[truncated]"
        return formatted
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        formatted = [
            _format_optional_context(item, depth=depth + 1) for item in value[:MAX_CONTEXT_ITEMS]
        ]
        if len(value) > MAX_CONTEXT_ITEMS:
            formatted.append("[truncated]")
        return formatted
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return format_diagnostic_text(str(value))


def configure_logging() -> None:
    root = logging.getLogger()
    formatter = StructuredFormatter()
    root.setLevel(logging.INFO)
    if not root.handlers:
        root.addHandler(logging.StreamHandler())
    for handler in root.handlers:
        handler.setFormatter(formatter)
