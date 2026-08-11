import logging

from stt_vault.core.config import Settings
from stt_vault.core.diagnostics.logging import job_log_context, log_exception_diagnostic
from stt_vault.core.models.records import ErrorRecord
from stt_vault.persistence.sqlite_database import SqliteDatabase

logger = logging.getLogger(__name__)


def classify_worker_failure(error: Exception) -> ErrorRecord:
    module = error.__class__.__module__
    if module.startswith(("openai", "httpx", "requests")):
        return ErrorRecord("provider", "An external provider request failed")
    if isinstance(error, OSError):
        return ErrorRecord("filesystem", "A local processing operation failed")
    return ErrorRecord("processing", "Asset processing failed")


class WorkerFailureHandler:
    """Classify, log, and persist terminal worker failures."""

    def __init__(self, settings: Settings, repository: SqliteDatabase) -> None:
        self.settings = settings
        self.repository = repository

    def handle(self, asset_id: str, error: Exception) -> None:
        log_exception_diagnostic(
            logger,
            "worker job failed",
            error,
            event_name="worker.job_failed",
            context=job_log_context(self.repository, asset_id),
        )
        record = classify_worker_failure(error)
        log_exception_diagnostic(
            logger,
            "worker failure categorized",
            error,
            event_name="worker.failure_categorized",
            context=job_log_context(self.repository, asset_id),
        )
        self.repository.mark_failed(asset_id, record)
