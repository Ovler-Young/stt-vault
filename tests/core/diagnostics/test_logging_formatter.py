import json
import logging
import math
import sys
from collections.abc import Sequence

from stt_vault.core.diagnostics.logging import StructuredFormatter


class NonSliceableSequence(Sequence[int]):
    """Sequence implementation that permits only integer indexes."""

    def __init__(self, items: list[int]) -> None:
        self._items = tuple(items)

    def __getitem__(self, index: int) -> int:
        if isinstance(index, slice):
            raise TypeError("slice indexes are not supported")
        return self._items[index]

    def __len__(self) -> int:
        return len(self._items)


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


def test_structured_formatter_redacts_sensitive_command_text() -> None:
    formatter = StructuredFormatter()
    record = logging.makeLogRecord(
        {
            "name": "stt_vault.processing.visual",
            "levelno": logging.ERROR,
            "levelname": "ERROR",
            "msg": "ffmpeg failed",
            "command": "ffmpeg -i /srv/private/clip.wav --api-key=secret-command-key",
        }
    )

    rendered = formatter.format(record)

    assert "/srv/private/clip.wav" not in rendered
    assert "secret-command-key" not in rendered
    assert json.loads(rendered)["command"] == "ffmpeg -i [path] --[redacted]"


def test_structured_formatter_redacts_secret_key_variants() -> None:
    formatter = StructuredFormatter()
    record = logging.makeLogRecord(
        {
            "name": "stt_vault.processing.transcription",
            "levelno": logging.ERROR,
            "levelname": "ERROR",
            "msg": "provider failed",
            "details": "secret=plain client_secret=oauth-secret CLIENT_SECRET=upper",
        }
    )

    rendered = formatter.format(record)

    assert "plain" not in rendered
    assert "oauth-secret" not in rendered
    assert "upper" not in rendered
    assert json.loads(rendered)["details"] == ("[redacted] [redacted] [redacted]")


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


def test_structured_formatter_bounds_mapping_context() -> None:
    formatter = StructuredFormatter()
    record = logging.makeLogRecord(
        {
            "name": "stt_vault.routes.assets.details",
            "levelno": logging.ERROR,
            "levelname": "ERROR",
            "msg": "summary failed",
            "context": {f"key-{index}": index for index in range(100)},
        }
    )

    event = json.loads(formatter.format(record))
    context = event["context"]

    assert len(context) == 51
    assert context["key-49"] == 49
    assert context["_truncated"] == "[truncated]"


def test_structured_formatter_sanitizes_mapping_keys_and_nonfinite_floats() -> None:
    formatter = StructuredFormatter()
    record = logging.makeLogRecord(
        {
            "name": "stt_vault.workers.worker",
            "levelno": logging.ERROR,
            "levelname": "ERROR",
            "msg": "worker failed",
            "return_code": math.nan,
            "details": {
                "/srv/private/clip.wav token=secret-value": math.nan,
                "positive": math.inf,
                "negative": -math.inf,
            },
        }
    )

    rendered = formatter.format(record)
    event = json.loads(rendered)

    assert "/srv/private/clip.wav" not in rendered
    assert "secret-value" not in rendered
    assert "NaN" not in rendered
    assert "Infinity" not in rendered
    assert event["return_code"] is None
    assert event["details"] == {
        "[path] [redacted]": None,
        "positive": None,
        "negative": None,
    }


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


def test_structured_formatter_bounds_non_sliceable_sequence_context() -> None:
    formatter = StructuredFormatter()
    record = logging.makeLogRecord(
        {
            "name": "stt_vault.workers.worker",
            "levelno": logging.ERROR,
            "levelname": "ERROR",
            "msg": "worker failed",
            "details": NonSliceableSequence(list(range(51))),
        }
    )

    event = json.loads(formatter.format(record))

    assert event["details"] == [*range(50), "[truncated]"]
