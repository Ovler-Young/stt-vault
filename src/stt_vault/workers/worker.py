import logging
import threading
import uuid
from collections.abc import Callable
from typing import Protocol

from stt_vault.core.logging_config import job_log_context
from stt_vault.core.process_diagnostics import format_diagnostic_text
from stt_vault.core.settings import Settings
from stt_vault.core.types import AssetRecord, ErrorRecord, ExportPaths, TranscriptSegment
from stt_vault.processing.diarization import DiarizerManager

from .worker_completion import CompletionStage
from .worker_exports import TranscriptExportStage, VisualEventStage
from .worker_media import DiarizationStage, MediaPreparationStage
from .worker_models import PreparedAsset
from .worker_transcription import TranscriptionStage
from .worker_workspace import ProcessingWorkspace

logger = logging.getLogger(__name__)


class WorkerRepository(Protocol):
    """Database operations used by job orchestration."""

    def claim_next_job(self, owner: str, lease_seconds: int) -> str | None: ...

    def renew_job_claim(self, asset_id: str, owner: str, lease_seconds: int) -> bool: ...

    def mark_failed(self, asset_id: str, error: ErrorRecord) -> None: ...

    def get_asset(self, asset_id: str) -> AssetRecord | None: ...

    def list_transcript_chunks(self, asset_id: str) -> list[TranscriptSegment]: ...


class SqliteWorkerRepository:
    def __init__(self, settings: Settings) -> None:
        self.db_path = settings.stt_db_path

    def claim_next_job(self, owner: str, lease_seconds: int) -> str | None:
        from stt_vault.persistence import db

        return db.claim_next_job(self.db_path, owner, lease_seconds)

    def renew_job_claim(self, asset_id: str, owner: str, lease_seconds: int) -> bool:
        from stt_vault.persistence import db

        return db.renew_job_claim(self.db_path, asset_id, owner, lease_seconds)

    def mark_failed(self, asset_id: str, error: ErrorRecord) -> None:
        from stt_vault.persistence import db

        db.mark_failed(self.db_path, asset_id, error)

    def get_asset(self, asset_id: str) -> AssetRecord | None:
        from stt_vault.persistence import db

        return db.get_asset(self.db_path, asset_id)

    def list_transcript_chunks(self, asset_id: str) -> list[TranscriptSegment]:
        from stt_vault.persistence import db

        return db.list_transcript_chunks(self.db_path, asset_id)


def create_diarizer(settings: Settings) -> DiarizerManager:
    return DiarizerManager(
        device=settings.senko_device,
        idle_timeout_seconds=settings.diarizer_idle_timeout_seconds,
        use_batched_embeddings=settings.senko_batched_embeddings,
        fbank_batch_segments=settings.senko_fbank_batch_segments,
    )


class Worker:
    def __init__(
        self,
        settings: Settings,
        *,
        diarizer_factory: Callable[[Settings], DiarizerManager] = create_diarizer,
        media_preparation_stage_factory: Callable[
            [Settings], MediaPreparationStage
        ] = MediaPreparationStage,
        diarization_stage_factory: Callable[
            [Settings, DiarizerManager], DiarizationStage
        ] = DiarizationStage,
        transcription_stage_factory: Callable[[Settings], TranscriptionStage] = TranscriptionStage,
        visual_event_stage_factory: Callable[[Settings], VisualEventStage] = VisualEventStage,
        transcript_export_stage_factory: Callable[
            [Settings], TranscriptExportStage
        ] = TranscriptExportStage,
        completion_stage_factory: Callable[[Settings], CompletionStage] = CompletionStage,
        repository: WorkerRepository | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository or SqliteWorkerRepository(settings)
        self.stop_event = threading.Event()
        self.claim_owner = uuid.uuid4().hex
        self.thread = threading.Thread(target=self.run, name="stt-vault-worker", daemon=True)
        self.diarizer = diarizer_factory(settings)
        self.media_preparation = media_preparation_stage_factory(settings)
        self.diarization = diarization_stage_factory(settings, self.diarizer)
        self.visual_events = visual_event_stage_factory(settings)
        self.transcription = transcription_stage_factory(settings)
        self.transcript_exports = transcript_export_stage_factory(settings)
        self.completion = completion_stage_factory(settings)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=10)

    def run(self) -> None:
        while not self.stop_event.is_set():
            asset_id = self.repository.claim_next_job(
                self.claim_owner, self.settings.job_lease_seconds
            )
            if asset_id is None:
                self.diarizer.maybe_unload()
                self.stop_event.wait(2)
                continue

            claim_stop_event = threading.Event()
            claim_renewer = threading.Thread(
                target=self._renew_claim_until_complete,
                args=(asset_id, claim_stop_event),
                name=f"stt-vault-claim-{asset_id}",
                daemon=True,
            )
            claim_renewer.start()
            try:
                self.process_asset(asset_id)
            except Exception as exc:
                logger.exception(
                    "worker job failed",
                    extra={
                        **job_log_context(self.settings.stt_db_path, asset_id),
                        "event_name": "worker.job_failed",
                    },
                )
                self.repository.mark_failed(asset_id, _failure_record(self.settings, asset_id, exc))
            finally:
                claim_stop_event.set()
                claim_renewer.join()

    def _renew_claim_until_complete(self, asset_id: str, stop_event: threading.Event) -> None:
        interval_seconds = max(1, self.settings.job_lease_seconds // 3)
        while not stop_event.wait(interval_seconds):
            if not self.repository.renew_job_claim(
                asset_id, self.claim_owner, self.settings.job_lease_seconds
            ):
                return

    def process_asset(self, asset_id: str) -> None:
        asset = self.repository.get_asset(asset_id)
        if asset is None:
            return
        with ProcessingWorkspace(self.settings.tmp_dir, asset_id) as work_dir:
            wav_path, duration = self.media_preparation.prepare(asset_id, asset)
            prepared = self.diarization.diarize(asset_id, wav_path, duration)
            transcript_segments, error = self.transcription.transcribe(
                asset_id, asset, prepared, work_dir
            )
            if error is not None:
                exports = self._write_exports(
                    asset_id, asset, prepared, transcript_segments, partial=True
                )
                self.completion.complete_partial(
                    asset_id, prepared, transcript_segments, exports, error
                )
                return

            persisted_segments = self.repository.list_transcript_chunks(asset_id)
            completed_segments = persisted_segments or transcript_segments
            exports = self._write_exports(
                asset_id, asset, prepared, completed_segments, partial=False
            )
            self.completion.complete(asset_id, prepared, completed_segments, exports)

    def _write_exports(
        self,
        asset_id: str,
        asset: AssetRecord,
        prepared: PreparedAsset,
        segments: list[TranscriptSegment],
        *,
        partial: bool,
    ) -> ExportPaths:
        exports = self.transcript_exports.write(
            asset_id, asset, prepared, segments, partial=partial
        )
        exports.update(self.visual_events.detect(asset_id, asset))
        return exports


def _failure_record(settings: Settings, asset_id: str, error: Exception) -> ErrorRecord:
    module = error.__class__.__module__
    if module.startswith(("openai", "httpx", "requests")):
        category = "provider"
        message = "An external provider request failed"
    elif isinstance(error, OSError):
        category = "filesystem"
        message = "A local processing operation failed"
    else:
        category = "processing"
        message = "Asset processing failed"
    # Keep the redacted cause in filtered logs only; persisted records are user-facing.
    logger.error(
        "worker failure categorized",
        extra={
            **job_log_context(settings.stt_db_path, asset_id),
            "event_name": "worker.failure_categorized",
            "cause": format_diagnostic_text(str(error)),
        },
    )
    return {"category": category, "message": message}
