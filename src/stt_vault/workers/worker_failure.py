import logging
from typing import Protocol

from stt_vault.core.config import Settings
from stt_vault.core.diagnostics.logging import job_log_context, log_exception_diagnostic
from stt_vault.core.models.records import ErrorRecord

logger = logging.getLogger(__name__)


def classify_worker_failure(error: Exception) -> ErrorRecord:
    module = error.__class__.__module__
    if module.startswith(("openai", "httpx", "requests")):
        return {"category": "provider", "message": "An external provider request failed"}
    if isinstance(error, OSError):
        return {"category": "filesystem", "message": "A local processing operation failed"}
    return {"category": "processing", "message": "Asset processing failed"}


class FailureRepository(Protocol):
    def mark_failed(self, asset_id: str, error: ErrorRecord) -> None: ...


class WorkerFailureHandler:
    """Classify, log, and persist terminal worker failures."""

    def __init__(self, settings: Settings, repository: FailureRepository) -> None:
        self.settings = settings
        self.repository = repository

    def handle(self, asset_id: str, error: Exception) -> None:
        log_exception_diagnostic(
            logger,
            "worker job failed",
            error,
            event_name="worker.job_failed",
            context=job_log_context(self.settings.stt_db_path, asset_id),
        )
        record = classify_worker_failure(error)
        log_exception_diagnostic(
            logger,
            "worker failure categorized",
            error,
            event_name="worker.failure_categorized",
            context=job_log_context(self.settings.stt_db_path, asset_id),
        )
        self.repository.mark_failed(asset_id, record)
