from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol
from uuid import uuid4

from stt_vault.core.config import Settings
from stt_vault.core.models.records import (
    AssetRecord,
    CompleteTranscriptionProviderInvocation,
    ErrorRecord,
    FindProviderWorkItem,
    JobEventCreate,
    JobProgressUpdate,
    PreparedProviderInvocation,
    PrepareProviderWorkItem,
    ProviderInvocationTransition,
    ProviderMetadata,
    RetryProviderInvocation,
    TranscriptChunk,
    TranscriptChunkUpsert,
    TranscriptSegment,
)
from stt_vault.persistence.sqlite_database import SqliteDatabase
from stt_vault.processing.diarization import match_speakers
from stt_vault.processing.media_transcoding import extract_audio_chunk
from stt_vault.processing.transcription import (
    ChunkDoneCallback,
    ChunkRetryCallback,
    SidecarInvocationLifecycle,
    SidecarPreparedRequest,
    SidecarRequestIdentity,
    Transcriber,
    build_transcription_plan,
    canonical_sidecar_request_hash,
    transcript_chunks_match_plan,
)

from .worker_models import PreparedAsset, TranscriptionWork, apply_speaker_names


@dataclass(frozen=True)
class TranscriberConfig:
    provider: Literal["openai", "mod-whisper-cpu"]
    api_key: str
    base_url: str
    model: str
    prompt: str
    concurrency: int
    retry_seconds: int
    max_retries: int
    retry_backoff_seconds: list[int]
    on_chunk_done: ChunkDoneCallback
    on_chunk_retry: ChunkRetryCallback


class TranscriberFactory(Protocol):
    def __call__(self, config: TranscriberConfig) -> Transcriber: ...


def create_transcriber(config: TranscriberConfig) -> Transcriber:
    return Transcriber(
        provider=config.provider,
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
        prompt=config.prompt,
        concurrency=config.concurrency,
        retry_seconds=config.retry_seconds,
        max_retries=config.max_retries,
        retry_backoff_seconds=config.retry_backoff_seconds,
        on_chunk_done=config.on_chunk_done,
        on_chunk_retry=config.on_chunk_retry,
    )


@dataclass(frozen=True)
class ProviderJobContext:
    job_id: str
    run_attempt: int
    work_generation: int


class _SidecarInvocation(SidecarInvocationLifecycle):
    def __init__(self, database: SqliteDatabase, invocation: PreparedProviderInvocation) -> None:
        self.database = database
        self.work_item_id = str(invocation.work_item_id)
        self.invocation_attempt = int(invocation.invocation_attempt)
        self.run_attempt = int(invocation.run_attempt)
        self.idempotency_key = invocation.idempotency_key
        self.correlation_id = invocation.correlation_id
        self.state = str(invocation.state)
        self.provider_metadata: Mapping[str, str] | None = None
        self.timing_ms: int | None = None

    def _transition(
        self, expected_state: str, to_state: str, error: Exception | None = None
    ) -> bool:
        if self.state != expected_state:
            return self.state == to_state
        result = self.database.transition_provider_invocation(
            ProviderInvocationTransition(
                self.work_item_id,
                self.invocation_attempt,
                expected_state,
                to_state,
                self.run_attempt,
                error_category=(getattr(error, "category", "provider") if error else None),
            )
        )
        if result.applied:
            self.state = to_state
        return result.applied

    def sent(self) -> bool:
        return self._transition("prepared", "sent")

    def accepted(
        self, provider_metadata: Mapping[str, str] | None = None, timing_ms: int | None = None
    ) -> bool:
        self.provider_metadata = provider_metadata
        self.timing_ms = timing_ms
        if self.state != "sent":
            return self.state == "accepted"
        result = self.database.transition_provider_invocation(
            ProviderInvocationTransition(
                self.work_item_id,
                self.invocation_attempt,
                "sent",
                "accepted",
                self.run_attempt,
                provider_metadata=ProviderMetadata(**provider_metadata)
                if provider_metadata
                else None,
                timing_ms=timing_ms,
            )
        )
        if result.applied:
            self.state = "accepted"
        return result.applied

    def retry(self, error: Exception) -> SidecarRequestIdentity | None:
        if self.state not in {"prepared", "sent", "accepted"}:
            return None
        invocation = self.database.retry_provider_invocation(
            RetryProviderInvocation(
                self.work_item_id,
                self.state,
                self.run_attempt,
                str(uuid4()),
                getattr(error, "category", "provider"),
            )
        )
        if invocation is None:
            return None
        self.invocation_attempt = invocation.invocation_attempt
        self.run_attempt = invocation.run_attempt
        self.idempotency_key = invocation.idempotency_key
        self.correlation_id = invocation.correlation_id
        self.state = invocation.state
        return SidecarRequestIdentity(
            idempotency_key=self.idempotency_key,
            correlation_id=self.correlation_id,
        )

    def completed(self) -> bool:
        return self._transition("accepted", "completed")

    def complete_transcript(
        self, asset_id: str, chunk_index: int, segment: TranscriptSegment
    ) -> bool:
        if self.state != "accepted":
            return self.state == "completed"
        result = self.database.complete_transcription_and_provider_invocation(
            CompleteTranscriptionProviderInvocation(
                self.work_item_id,
                self.invocation_attempt,
                self.run_attempt,
                asset_id,
                chunk_index,
                segment,
                self.invocation_attempt,
                ProviderMetadata(**self.provider_metadata) if self.provider_metadata else None,
                self.timing_ms,
            )
        )
        if result.applied:
            self.state = "completed"
        return result.applied

    def failed(self, error: Exception) -> bool:
        if self.state not in {"prepared", "sent", "accepted"}:
            return self.state == "failed"
        return self._transition(self.state, "failed", error)


class TranscriptChunkPersistence:
    def __init__(self, settings: Settings, database: SqliteDatabase) -> None:
        self.settings = settings
        self.database = database

    def prepare_work(
        self, asset_id: str, prepared: PreparedAsset
    ) -> tuple[TranscriptionWork, bool]:
        chunks = build_transcription_plan(
            prepared.raw_segments,
            max_seconds=self.settings.transcribe_chunk_seconds,
        )
        existing_chunks = self.database.list_transcript_chunks(asset_id)
        plan_changed = False
        if existing_chunks and not transcript_chunks_match_plan(existing_chunks, chunks):
            self.database.reset_transcript_chunks(asset_id)
            existing_chunks = []
            plan_changed = True
        completed_indexes = {
            chunk.chunk_index for chunk in existing_chunks if chunk.chunk_index is not None
        }
        return (
            TranscriptionWork(
                chunks=chunks,
                pending_chunks=[
                    chunk for chunk in chunks if chunk.chunk_index not in completed_indexes
                ],
                completed_chunks=len(completed_indexes),
            ),
            plan_changed,
        )

    def save_success(self, asset_id: str, index: int, result: TranscriptSegment) -> None:
        self.database.upsert_transcript_chunk(
            TranscriptChunkUpsert(asset_id, index, result, result.attempts or 1)
        )

    def recorded_segments(self, asset_id: str) -> list[TranscriptSegment]:
        return self.database.list_transcript_chunks(asset_id)


class SpeakerReconciler:
    def __init__(self, settings: Settings, database: SqliteDatabase) -> None:
        self.settings = settings
        self.database = database

    def reconcile(
        self, prepared: PreparedAsset, segments: list[TranscriptSegment]
    ) -> list[TranscriptSegment]:
        matches = match_speakers(
            prepared.speaker_centroids,
            self.database.list_speakers(),
            self.settings.speaker_similarity_threshold,
            embedding_space=prepared.embedding_space,
        )
        return apply_speaker_names(segments, matches)


class TranscriptionProgressEvents:
    def __init__(self, settings: Settings, database: SqliteDatabase) -> None:
        self.settings = settings
        self.database = database

    def start(self, asset_id: str, work: TranscriptionWork, *, plan_changed: bool) -> None:
        self.database.update_stage(asset_id=asset_id, stage="transcribing speech")
        if plan_changed:
            self.database.add_event(
                JobEventCreate(
                    asset_id,
                    "info",
                    "transcribing speech",
                    "Transcript chunk plan changed; restarting transcription",
                )
            )
        self.database.update_progress(
            JobProgressUpdate(
                asset_id, total_chunks=len(work.chunks), done_chunks=work.completed_chunks
            )
        )

    def record_success(self, asset_id: str, work: TranscriptionWork, index: int) -> None:
        work.completed_chunks += 1
        self.database.update_progress(
            JobProgressUpdate(
                asset_id, done_chunks=work.completed_chunks, failed_chunks=work.failed_chunks
            )
        )
        self.database.add_event(
            JobEventCreate(
                asset_id,
                "info",
                "transcribing speech",
                f"Chunk {index + 1} transcribed",
                ErrorRecord("transcription", f"Chunk {index + 1} transcribed"),
            )
        )

    def record_retry(
        self,
        asset_id: str,
        work: TranscriptionWork,
        index: int,
        attempt: int,
        _error: Exception,
        retry_at: int,
    ) -> None:
        work.failed_chunks += 1
        self.database.update_progress(
            JobProgressUpdate(asset_id, failed_chunks=work.failed_chunks, next_retry_at=retry_at)
        )
        self.database.add_event(
            JobEventCreate(
                asset_id,
                "warning",
                "transcribing speech",
                (
                    f"Chunk {index + 1} failed on attempt {attempt}; "
                    "OpenAI cooldown active until retry"
                ),
                ErrorRecord("provider", "Transcription provider request failed"),
            )
        )


class TranscriptionStage:
    def __init__(
        self,
        settings: Settings,
        database: SqliteDatabase,
        *,
        transcriber_factory: TranscriberFactory = create_transcriber,
        chunk_persistence: TranscriptChunkPersistence | None = None,
        speaker_reconciler: SpeakerReconciler | None = None,
        progress_events: TranscriptionProgressEvents | None = None,
    ) -> None:
        self.settings = settings
        self.transcriber_factory = transcriber_factory
        self.database = database
        self.chunk_persistence = chunk_persistence or TranscriptChunkPersistence(settings, database)
        self.speaker_reconciler = speaker_reconciler or SpeakerReconciler(settings, database)
        self.progress_events = progress_events or TranscriptionProgressEvents(settings, database)

    def transcribe(
        self,
        asset_id: str,
        asset: AssetRecord,
        prepared: PreparedAsset,
        work_dir: Path,
        job_context: ProviderJobContext | None = None,
    ) -> tuple[list[TranscriptSegment], Exception | None]:
        work, plan_changed = self.chunk_persistence.prepare_work(asset_id, prepared)
        self.progress_events.start(asset_id, work, plan_changed=plan_changed)

        def on_chunk_done(index: int, result: TranscriptSegment) -> None:
            enriched = self.speaker_reconciler.reconcile(prepared, [result])[0]
            self.chunk_persistence.save_success(asset_id, index, enriched)
            self.progress_events.record_success(asset_id, work, index)

        def on_chunk_retry(index: int, attempt: int, error: Exception, retry_at: int) -> None:
            self.progress_events.record_retry(asset_id, work, index, attempt, error, retry_at)

        transcriber = self.transcriber_factory(
            TranscriberConfig(
                provider=self.settings.stt_transcription_provider,
                api_key=self.settings.openai_api_key,
                base_url=self.settings.openai_base_url,
                model=self.settings.openai_transcribe_model,
                prompt=self.settings.openai_transcribe_prompt,
                concurrency=self.settings.openai_concurrency,
                retry_seconds=self.settings.openai_retry_seconds,
                max_retries=self.settings.openai_max_retries,
                retry_backoff_seconds=self.settings.parsed_openai_retry_backoff_seconds,
                on_chunk_done=on_chunk_done,
                on_chunk_retry=on_chunk_retry,
            )
        )
        sidecar_requests: dict[int, SidecarPreparedRequest] | None = None
        if self.settings.stt_transcription_provider == "mod-whisper-cpu":
            if job_context is None:
                return self.chunk_persistence.recorded_segments(asset_id), ValueError(
                    "sidecar transcription requires an active job context"
                )
            sidecar_requests = self._prepare_sidecar_requests(
                asset_id,
                prepared,
                work,
                work.pending_chunks,
                work_dir,
                job_context,
            )
        try:
            transcribe_kwargs: dict[str, object] = {"asset_id": asset_id}
            if sidecar_requests is not None:
                transcribe_kwargs["sidecar_prepared_requests"] = sidecar_requests
            segments = transcriber.transcribe_chunks(
                prepared.wav_path, work.pending_chunks, work_dir, **transcribe_kwargs
            )
        except Exception as error:
            return self.chunk_persistence.recorded_segments(asset_id), error
        return self.speaker_reconciler.reconcile(prepared, segments), None

    def _prepare_sidecar_requests(
        self,
        asset_id: str,
        prepared: PreparedAsset,
        work: TranscriptionWork,
        chunks: list[TranscriptChunk],
        work_dir: Path,
        job_context: ProviderJobContext,
    ) -> dict[int, SidecarPreparedRequest]:
        image_digest = self.settings.mod_whisper_cpu_image_digest
        requests: dict[int, SidecarPreparedRequest] = {}
        for chunk in chunks:
            index = chunk.chunk_index
            existing = self.database.find_provider_work_item(
                FindProviderWorkItem(
                    job_context.job_id,
                    asset_id,
                    "transcription",
                    "mod-whisper-cpu",
                    image_digest,
                    f"chunk:{index}",
                    job_context.work_generation,
                )
            )
            audio_path = work_dir / f"chunk-{index:06d}.wav"
            extract_audio_chunk(prepared.wav_path, audio_path, chunk.start, chunk.end)
            provisional_key = existing.idempotency_key if existing is not None else str(uuid4())
            request_hash = canonical_sidecar_request_hash(
                asset_id=asset_id,
                chunk=chunk,
                idempotency_key=provisional_key,
                prompt=self.settings.openai_transcribe_prompt or None,
                audio_path=audio_path,
            )
            invocation = self.database.prepare_provider_work_item(
                PrepareProviderWorkItem(
                    str(uuid4()),
                    job_context.job_id,
                    asset_id,
                    "transcription",
                    f"chunk:{index}",
                    job_context.run_attempt,
                    provisional_key,
                    request_hash,
                    "mod-whisper-cpu",
                    image_digest,
                    job_context.work_generation,
                    str(uuid4()),
                )
            )
            stored_hash = str(invocation.request_hash)
            if stored_hash != request_hash:
                raise ValueError("regenerated sidecar request does not match its durable hash")
            lifecycle = _SidecarInvocation(self.database, invocation)
            requests[index] = SidecarPreparedRequest(
                audio_path=audio_path,
                request_hash=stored_hash,
                identity=SidecarRequestIdentity(
                    idempotency_key=str(invocation.idempotency_key),
                    correlation_id=str(invocation.correlation_id),
                ),
                lifecycle=lifecycle,
                on_completed=lambda result, index=index, lifecycle=lifecycle: (
                    self._complete_sidecar_chunk(asset_id, prepared, work, index, result, lifecycle)
                ),
            )
        return requests

    def _complete_sidecar_chunk(
        self,
        asset_id: str,
        prepared: PreparedAsset,
        work: TranscriptionWork,
        index: int,
        result: TranscriptSegment,
        lifecycle: _SidecarInvocation,
    ) -> None:
        enriched = self.speaker_reconciler.reconcile(prepared, [result])[0]
        if not lifecycle.complete_transcript(asset_id, index, enriched):
            raise RuntimeError("sidecar invocation completion transition was stale")
        self.progress_events.record_success(asset_id, work, index)
