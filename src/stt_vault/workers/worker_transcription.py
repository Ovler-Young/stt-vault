from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from stt_vault.core.config import Settings
from stt_vault.core.models.records import AssetRecord, KnownSpeaker, TranscriptSegment
from stt_vault.persistence.workspace.worker_repository import SqliteWorkerRepository
from stt_vault.processing.diarization import match_speakers
from stt_vault.processing.transcription import (
    ChunkDoneCallback,
    ChunkRetryCallback,
    Transcriber,
    build_transcription_plan,
    transcript_chunks_match_plan,
)

from .worker_models import PreparedAsset, TranscriptionWork, apply_speaker_names


@dataclass(frozen=True)
class TranscriberConfig:
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


class TranscriptionRepository(Protocol):
    def list_transcript_chunks(self, asset_id: str) -> list[TranscriptSegment]: ...

    def reset_transcript_chunks(self, asset_id: str) -> None: ...

    def upsert_transcript_chunk(
        self, asset_id: str, index: int, result: TranscriptSegment, *, attempts: int
    ) -> None: ...

    def list_speakers(self) -> list[KnownSpeaker]: ...

    def update_stage(self, asset_id: str, stage: str) -> None: ...

    def add_event(
        self,
        asset_id: str,
        level: str,
        stage: str,
        message: str,
        payload: dict[str, object] | None = None,
    ) -> None: ...

    def update_progress(self, asset_id: str, **kwargs: int | None) -> None: ...


class TranscriptChunkPersistence:
    def __init__(
        self, settings: Settings, repository: TranscriptionRepository | None = None
    ) -> None:
        self.settings = settings
        self.repository = repository or SqliteWorkerRepository(settings.stt_db_path)

    def prepare_work(
        self, asset_id: str, prepared: PreparedAsset
    ) -> tuple[TranscriptionWork, bool]:
        chunks = build_transcription_plan(
            {"raw_segments": prepared.raw_segments},
            max_seconds=self.settings.transcribe_chunk_seconds,
        )
        existing_chunks = self.repository.list_transcript_chunks(asset_id)
        plan_changed = False
        if existing_chunks and not transcript_chunks_match_plan(existing_chunks, chunks):
            self.repository.reset_transcript_chunks(asset_id)
            existing_chunks = []
            plan_changed = True
        completed_indexes = {
            int(chunk["chunk_index"])
            for chunk in existing_chunks
            if chunk.get("status") == "success"
        }
        return (
            TranscriptionWork(
                chunks=chunks,
                pending_chunks=[
                    chunk for chunk in chunks if int(chunk["chunk_index"]) not in completed_indexes
                ],
                completed_chunks=len(completed_indexes),
            ),
            plan_changed,
        )

    def save_success(self, asset_id: str, index: int, result: TranscriptSegment) -> None:
        self.repository.upsert_transcript_chunk(
            asset_id, index, result, attempts=int(result.get("attempts", 1))
        )

    def recorded_segments(self, asset_id: str) -> list[TranscriptSegment]:
        return self.repository.list_transcript_chunks(asset_id)


class SpeakerReconciler:
    def __init__(
        self, settings: Settings, repository: TranscriptionRepository | None = None
    ) -> None:
        self.settings = settings
        self.repository = repository or SqliteWorkerRepository(settings.stt_db_path)

    def reconcile(
        self, prepared: PreparedAsset, segments: list[TranscriptSegment]
    ) -> list[TranscriptSegment]:
        matches = match_speakers(
            prepared.speaker_centroids,
            self.repository.list_speakers(),
            self.settings.speaker_similarity_threshold,
        )
        return apply_speaker_names(segments, matches)


class TranscriptionProgressEvents:
    def __init__(
        self, settings: Settings, repository: TranscriptionRepository | None = None
    ) -> None:
        self.settings = settings
        self.repository = repository or SqliteWorkerRepository(settings.stt_db_path)

    def start(self, asset_id: str, work: TranscriptionWork, *, plan_changed: bool) -> None:
        self.repository.update_stage(asset_id, "transcribing speech")
        if plan_changed:
            self.repository.add_event(
                asset_id,
                "info",
                "transcribing speech",
                "Transcript chunk plan changed; restarting transcription",
            )
        self.repository.update_progress(
            asset_id,
            total_chunks=len(work.chunks),
            done_chunks=work.completed_chunks,
        )

    def record_success(self, asset_id: str, work: TranscriptionWork, index: int) -> None:
        work.completed_chunks += 1
        self.repository.update_progress(
            asset_id,
            done_chunks=work.completed_chunks,
            failed_chunks=work.failed_chunks,
            next_retry_at=None,
        )
        self.repository.add_event(
            asset_id,
            "info",
            "transcribing speech",
            f"Chunk {index + 1} transcribed",
            {"chunk_index": index, "done_chunks": work.completed_chunks},
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
        self.repository.update_progress(
            asset_id,
            failed_chunks=work.failed_chunks,
            next_retry_at=retry_at,
        )
        self.repository.add_event(
            asset_id,
            "warning",
            "transcribing speech",
            f"Chunk {index + 1} failed on attempt {attempt}; OpenAI cooldown active until retry",
            {
                "chunk_index": index,
                "attempt": attempt,
                "retry_at": retry_at,
                "note": (
                    "New OpenAI requests pause until retry; "
                    "already in-flight requests may still finish."
                ),
                "failure_category": "provider",
                "error": "Transcription provider request failed",
            },
        )


class TranscriptionStage:
    def __init__(
        self,
        settings: Settings,
        *,
        transcriber_factory: TranscriberFactory = create_transcriber,
        chunk_persistence: TranscriptChunkPersistence | None = None,
        speaker_reconciler: SpeakerReconciler | None = None,
        progress_events: TranscriptionProgressEvents | None = None,
        repository: TranscriptionRepository | None = None,
    ) -> None:
        self.settings = settings
        self.transcriber_factory = transcriber_factory
        repository = repository or SqliteWorkerRepository(settings.stt_db_path)
        self.chunk_persistence = chunk_persistence or TranscriptChunkPersistence(
            settings, repository
        )
        self.speaker_reconciler = speaker_reconciler or SpeakerReconciler(settings, repository)
        self.progress_events = progress_events or TranscriptionProgressEvents(settings, repository)

    def transcribe(
        self, asset_id: str, asset: AssetRecord, prepared: PreparedAsset, work_dir: Path
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
        try:
            segments = transcriber.transcribe_chunks(
                Path(asset["original_path"]), work.pending_chunks, work_dir
            )
        except Exception as error:
            return self.chunk_persistence.recorded_segments(asset_id), error
        return self.speaker_reconciler.reconcile(prepared, segments), None
