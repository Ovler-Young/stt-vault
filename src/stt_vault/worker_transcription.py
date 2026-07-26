from collections.abc import Callable
from pathlib import Path

from . import db
from .diarization import match_speakers
from .settings import Settings
from .transcription import Transcriber, build_transcription_plan, transcript_chunks_match_plan
from .types import AssetRecord, TranscriptSegment
from .worker_models import PreparedAsset, TranscriptionWork, apply_speaker_names

TranscriberFactory = Callable[..., Transcriber]


class TranscriptChunkPersistence:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def prepare_work(
        self, asset_id: str, prepared: PreparedAsset
    ) -> tuple[TranscriptionWork, bool]:
        chunks = build_transcription_plan(
            {"raw_segments": prepared.raw_segments},
            max_seconds=self.settings.transcribe_chunk_seconds,
        )
        existing_chunks = db.list_transcript_chunks(self.settings.stt_db_path, asset_id)
        plan_changed = False
        if existing_chunks and not transcript_chunks_match_plan(existing_chunks, chunks):
            db.reset_transcript_chunks(self.settings.stt_db_path, asset_id)
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
        db.upsert_transcript_chunk(
            self.settings.stt_db_path,
            asset_id,
            index,
            result,
            attempts=int(result.get("attempts", 1)),
        )

    def recorded_segments(self, asset_id: str) -> list[TranscriptSegment]:
        return db.list_transcript_chunks(self.settings.stt_db_path, asset_id)


class SpeakerReconciler:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def reconcile(
        self, prepared: PreparedAsset, segments: list[TranscriptSegment]
    ) -> list[TranscriptSegment]:
        matches = match_speakers(
            prepared.speaker_centroids,
            db.list_speakers(self.settings.stt_db_path),
            self.settings.speaker_similarity_threshold,
        )
        return apply_speaker_names(segments, matches)


class TranscriptionProgressEvents:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def start(self, asset_id: str, work: TranscriptionWork, *, plan_changed: bool) -> None:
        db.update_stage(self.settings.stt_db_path, asset_id, "transcribing speech")
        if plan_changed:
            db.add_event(
                self.settings.stt_db_path,
                asset_id,
                "info",
                "transcribing speech",
                "Transcript chunk plan changed; restarting transcription",
            )
        db.update_progress(
            self.settings.stt_db_path,
            asset_id,
            total_chunks=len(work.chunks),
            done_chunks=work.completed_chunks,
        )

    def record_success(self, asset_id: str, work: TranscriptionWork, index: int) -> None:
        work.completed_chunks += 1
        db.update_progress(
            self.settings.stt_db_path,
            asset_id,
            done_chunks=work.completed_chunks,
            failed_chunks=work.failed_chunks,
            next_retry_at=None,
        )
        db.add_event(
            self.settings.stt_db_path,
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
        db.update_progress(
            self.settings.stt_db_path,
            asset_id,
            failed_chunks=work.failed_chunks,
            next_retry_at=retry_at,
        )
        db.add_event(
            self.settings.stt_db_path,
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
        transcriber_factory: TranscriberFactory = Transcriber,
        chunk_persistence: TranscriptChunkPersistence | None = None,
        speaker_reconciler: SpeakerReconciler | None = None,
        progress_events: TranscriptionProgressEvents | None = None,
    ) -> None:
        self.settings = settings
        self.transcriber_factory = transcriber_factory
        self.chunk_persistence = chunk_persistence or TranscriptChunkPersistence(settings)
        self.speaker_reconciler = speaker_reconciler or SpeakerReconciler(settings)
        self.progress_events = progress_events or TranscriptionProgressEvents(settings)

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
        try:
            segments = transcriber.transcribe_chunks(
                Path(asset["original_path"]), work.pending_chunks, work_dir
            )
        except Exception as error:
            return self.chunk_persistence.recorded_segments(asset_id), error
        return self.speaker_reconciler.reconcile(prepared, segments), None
