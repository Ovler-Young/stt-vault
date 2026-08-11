import threading
import uuid
from collections.abc import Callable

from stt_vault.core.config import Settings
from stt_vault.core.models.persistence_errors import StaleClaimError
from stt_vault.core.models.records import (
    AssetRecord,
    ClaimNextJob,
    ClaimRecoverableJobs,
    CompleteProviderRecovery,
    ExportPaths,
    RecoveryProviderEntry,
    RecoveryProviderOutcome,
    RenewJobClaim,
    TranscriptSegment,
)
from stt_vault.persistence.sqlite_database import SqliteDatabase
from stt_vault.processing.diarization import DiarizerManager
from stt_vault.processing.transcription import SidecarProviderError, SidecarTranscriptionClient

from .worker_completion import CompletionStage
from .worker_exports import TranscriptExportStage, VisualEventStage
from .worker_failure import WorkerFailureHandler
from .worker_media import DiarizationStage, MediaPreparationStage
from .worker_models import PreparedAsset
from .worker_transcription import ProviderJobContext, TranscriptionStage
from .worker_workspace import ProcessingWorkspace


def create_diarizer(settings: Settings) -> DiarizerManager:
    return DiarizerManager(
        device=settings.senko_device,
        idle_timeout_seconds=settings.diarizer_idle_timeout_seconds,
        embedding_space=settings.senko_embedding_space,
    )


class Worker:
    def __init__(
        self,
        settings: Settings,
        *,
        diarizer_factory: Callable[[Settings], DiarizerManager] = create_diarizer,
        media_preparation_stage_factory: Callable[
            [Settings, SqliteDatabase], MediaPreparationStage
        ] = MediaPreparationStage,
        diarization_stage_factory: Callable[
            [Settings, DiarizerManager, SqliteDatabase], DiarizationStage
        ] = DiarizationStage,
        transcription_stage_factory: Callable[
            [Settings, SqliteDatabase], TranscriptionStage
        ] = TranscriptionStage,
        visual_event_stage_factory: Callable[
            [Settings, SqliteDatabase], VisualEventStage
        ] = VisualEventStage,
        transcript_export_stage_factory: Callable[
            [Settings, SqliteDatabase], TranscriptExportStage
        ] = TranscriptExportStage,
        completion_stage_factory: Callable[
            [Settings, SqliteDatabase], CompletionStage
        ] = CompletionStage,
        database: SqliteDatabase,
    ) -> None:
        self.settings = settings
        self.database = database
        self.stop_event = threading.Event()
        self.claim_owner = uuid.uuid4().hex
        self.thread = threading.Thread(target=self.run, name="stt-vault-worker", daemon=True)
        self.diarizer = diarizer_factory(settings)
        self.media_preparation = media_preparation_stage_factory(settings, database)
        self.diarization = diarization_stage_factory(settings, self.diarizer, database)
        self.visual_events = visual_event_stage_factory(settings, database)
        self.transcription = transcription_stage_factory(settings, database)
        self.transcript_exports = transcript_export_stage_factory(settings, database)
        self.completion = completion_stage_factory(settings, database)
        self.failures = WorkerFailureHandler(settings, database)

    def start(self) -> None:
        self.thread.start()

    def recover_startup_jobs(self) -> None:
        claims = self.database.claim_recoverable_jobs(ClaimRecoverableJobs())
        clients: dict[tuple[str, str], SidecarTranscriptionClient] = {}
        for command in claims.commands:
            try:
                outcomes = tuple(
                    self._recover_provider_entry(entry, clients) for entry in command.entries
                )
            except (OSError, SidecarProviderError, ValueError):
                continue
            try:
                self.database.complete_provider_recovery(
                    CompleteProviderRecovery(command, outcomes)
                )
            except StaleClaimError:
                continue

    @staticmethod
    def _recover_provider_entry(
        entry: RecoveryProviderEntry,
        clients: dict[tuple[str, str], SidecarTranscriptionClient],
    ) -> RecoveryProviderOutcome:
        if entry.expected_state == "prepared":
            return RecoveryProviderOutcome.prepared(entry)
        if (entry.role, entry.provider_id) == ("diarization", "senko"):
            return RecoveryProviderOutcome.abandoned(entry)
        if (entry.role, entry.provider_id) != ("transcription", "mod-whisper-cpu"):
            raise ValueError(f"unsupported recovery provider: {entry.role}/{entry.provider_id}")
        if entry.idempotency_key is None:
            raise ValueError("remote recovery entry is missing an idempotency key")
        client_key = (entry.role, entry.provider_id)
        client = clients.get(client_key)
        if client is None:
            client = SidecarTranscriptionClient()
            clients[client_key] = client
        status = client.cancel(entry.idempotency_key)
        return RecoveryProviderOutcome.cancelled(entry, http_status=status)

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=10)

    def run(self) -> None:
        while not self.stop_event.is_set():
            claim = self.database.claim_next_job(
                ClaimNextJob(self.claim_owner, self.settings.job_lease_seconds)
            )
            if claim is None:
                self.diarizer.maybe_unload()
                self.stop_event.wait(2)
                continue

            claim_stop_event = threading.Event()
            claim_renewer = threading.Thread(
                target=self._renew_claim_until_complete,
                args=(claim.asset_id, claim_stop_event),
                name=f"stt-vault-claim-{claim.asset_id}",
                daemon=True,
            )
            claim_renewer.start()
            try:
                context = self.database.get_active_job_context(claim.asset_id)
                if context is None:
                    raise RuntimeError("claimed job has no active context")
                self.process_asset(
                    claim.asset_id,
                    ProviderJobContext(
                        job_id=context.job_id,
                        run_attempt=context.run_attempt,
                        work_generation=context.run_attempt,
                    ),
                )
            except Exception as exc:
                self.failures.handle(claim.asset_id, exc)
            finally:
                claim_stop_event.set()
                claim_renewer.join()

    def _renew_claim_until_complete(self, asset_id: str, stop_event: threading.Event) -> None:
        interval_seconds = max(1, self.settings.job_lease_seconds // 3)
        while not stop_event.wait(interval_seconds):
            if not self.database.renew_job_claim(
                RenewJobClaim(asset_id, self.claim_owner, self.settings.job_lease_seconds)
            ):
                return

    def process_asset(self, asset_id: str, job_context: ProviderJobContext | None = None) -> None:
        asset = self.database.get_asset(asset_id)
        if asset is None:
            return
        with ProcessingWorkspace(self.settings.tmp_dir, asset_id) as work_dir:
            wav_path, duration = self.media_preparation.prepare(asset_id, asset)
            prepared = self.diarization.diarize(
                asset_id,
                wav_path,
                duration,
                job_id=job_context.job_id if job_context is not None else None,
                run_attempt=job_context.run_attempt if job_context is not None else None,
                work_generation=job_context.work_generation if job_context is not None else 1,
            )
            if job_context is None:
                transcript_segments, error = self.transcription.transcribe(
                    asset_id, asset, prepared, work_dir
                )
            else:
                transcript_segments, error = self.transcription.transcribe(
                    asset_id, asset, prepared, work_dir, job_context=job_context
                )
            if error is not None:
                exports = self._write_exports(
                    asset_id, asset, prepared, transcript_segments, partial=True
                )
                self.completion.complete_partial(
                    asset_id, prepared, transcript_segments, exports, error
                )
                return

            persisted_segments = self.database.list_transcript_chunks(asset_id)
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
        visual_exports = self.visual_events.detect(asset_id, asset)
        return ExportPaths(
            json=visual_exports.json or exports.json,
            whisper_json=visual_exports.whisper_json or exports.whisper_json,
            ai_text=visual_exports.ai_text or exports.ai_text,
            srt=visual_exports.srt or exports.srt,
            vtt=visual_exports.vtt or exports.vtt,
            hyperaudio_html=visual_exports.hyperaudio_html or exports.hyperaudio_html,
            rttm=visual_exports.rttm or exports.rttm,
            visual_events=visual_exports.visual_events or exports.visual_events,
        )
