import shutil
import threading
import uuid
from pathlib import Path
from typing import Any

from . import db
from .diarization import DiarizerManager, match_speakers, serialize_centroids
from .exports import write_exports
from .media import ffprobe_duration, to_wav_16k_mono
from .settings import Settings
from .transcription import Transcriber, build_chunks
from .visual import detect_slide_changes, write_visual_event_thumbnails, write_visual_events_export


class Worker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.stop_event = threading.Event()
        self.claim_owner = uuid.uuid4().hex
        self.thread = threading.Thread(target=self.run, name="stt-vault-worker", daemon=True)
        self.diarizer = DiarizerManager(
            device=settings.senko_device,
            idle_timeout_seconds=settings.diarizer_idle_timeout_seconds,
            use_batched_embeddings=settings.senko_batched_embeddings,
            fbank_batch_segments=settings.senko_fbank_batch_segments,
        )

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=10)

    def run(self) -> None:
        while not self.stop_event.is_set():
            asset_id = db.claim_next_job(
                self.settings.stt_db_path,
                self.claim_owner,
                self.settings.job_lease_seconds,
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
                self.process(asset_id)
            except Exception as exc:
                db.mark_failed(
                    self.settings.stt_db_path,
                    asset_id,
                    {"type": exc.__class__.__name__, "message": str(exc)},
                )
            finally:
                claim_stop_event.set()
                claim_renewer.join()

    def _renew_claim_until_complete(self, asset_id: str, stop_event: threading.Event) -> None:
        interval_seconds = max(1, self.settings.job_lease_seconds // 3)
        while not stop_event.wait(interval_seconds):
            if not db.renew_job_claim(
                self.settings.stt_db_path,
                asset_id,
                self.claim_owner,
                self.settings.job_lease_seconds,
            ):
                return

    def process(self, asset_id: str) -> None:
        asset = db.get_asset(self.settings.stt_db_path, asset_id)
        if asset is None:
            return

        original_path = Path(asset["original_path"])
        work_dir = self.settings.tmp_dir / asset_id
        if work_dir.exists():
            shutil.rmtree(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        wav_path = self.settings.media_dir / asset_id / "audio.16k.mono.wav"

        db.update_stage(self.settings.stt_db_path, asset_id, "probing media")
        duration = ffprobe_duration(original_path)

        db.update_stage(self.settings.stt_db_path, asset_id, "normalizing audio")
        to_wav_16k_mono(original_path, wav_path)

        db.update_stage(self.settings.stt_db_path, asset_id, "identifying speakers")
        diarization = self.diarizer.diarize(str(wav_path))
        if diarization is None:
            raise RuntimeError("No speech detected")

        centroids = serialize_centroids(diarization["speaker_centroids"])
        db.update_diarization_metadata(
            self.settings.stt_db_path,
            asset_id,
            wav_path=wav_path,
            duration=duration,
            diarization_stats=diarization["timing_stats"],
            raw_segments=diarization["raw_segments"],
            merged_segments=diarization["merged_segments"],
            speaker_centroids=centroids,
        )

        def current_speaker_matches() -> dict[str, dict[str, Any]]:
            return match_speakers(
                centroids,
                db.list_speakers(self.settings.stt_db_path),
                self.settings.speaker_similarity_threshold,
            )

        db.update_stage(self.settings.stt_db_path, asset_id, "transcribing speech")
        existing_chunks = db.list_transcript_chunks(self.settings.stt_db_path, asset_id)
        completed_chunk_indexes = {
            int(chunk["chunk_index"])
            for chunk in existing_chunks
            if chunk.get("status") == "success"
        }
        chunk_progress = {"done": 0, "failed": 0}

        def on_chunk_done(index: int, result: dict[str, Any]) -> None:
            enriched = apply_speaker_names([result], current_speaker_matches())[0]
            chunk_progress["done"] += 1
            db.upsert_transcript_chunk(
                self.settings.stt_db_path,
                asset_id,
                index,
                enriched,
                attempts=int(result.get("attempts", 1)),
            )
            db.update_progress(
                self.settings.stt_db_path,
                asset_id,
                done_chunks=chunk_progress["done"],
                failed_chunks=chunk_progress["failed"],
                next_retry_at=None,
            )
            db.add_event(
                self.settings.stt_db_path,
                asset_id,
                "info",
                "transcribing speech",
                f"Chunk {index + 1} transcribed",
                {"chunk_index": index, "done_chunks": chunk_progress["done"]},
            )

        def on_chunk_retry(index: int, attempt: int, exc: Exception, retry_at: int) -> None:
            chunk_progress["failed"] += 1
            db.update_progress(
                self.settings.stt_db_path,
                asset_id,
                failed_chunks=chunk_progress["failed"],
                next_retry_at=retry_at,
            )
            db.add_event(
                self.settings.stt_db_path,
                asset_id,
                "warning",
                "transcribing speech",
                (
                    f"Chunk {index + 1} failed on attempt {attempt}; "
                    "OpenAI cooldown active until retry"
                ),
                {
                    "chunk_index": index,
                    "attempt": attempt,
                    "retry_at": retry_at,
                    "note": (
                        "New OpenAI requests pause until retry; "
                        "already in-flight requests may still finish."
                    ),
                    "error_type": exc.__class__.__name__,
                    "error": str(exc),
                },
            )

        transcriber = Transcriber(
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
        chunks = build_chunks(
            diarization["merged_segments"],
            max_seconds=self.settings.transcribe_chunk_seconds,
            overlap_seconds=self.settings.transcribe_chunk_overlap_seconds,
        )
        chunks_to_transcribe = [
            chunk for chunk in chunks if int(chunk["chunk_index"]) not in completed_chunk_indexes
        ]
        chunk_progress["done"] = len(completed_chunk_indexes)
        db.update_progress(
            self.settings.stt_db_path,
            asset_id,
            total_chunks=len(chunks),
            done_chunks=chunk_progress["done"],
        )
        transcript_segments = []
        try:
            transcript_segments = transcriber.transcribe_chunks(
                original_path,
                chunks_to_transcribe,
                work_dir,
            )
            transcript_segments = apply_speaker_names(
                transcript_segments,
                current_speaker_matches(),
            )
        except Exception as exc:
            transcript_segments = db.list_transcript_chunks(self.settings.stt_db_path, asset_id)
            db.update_stage(self.settings.stt_db_path, asset_id, "writing partial exports")
            exports = write_exports(
                self.settings.exports_dir,
                asset_id,
                asset["filename"],
                transcript_segments,
                diarization["raw_segments"],
                self.settings.parsed_export_formats,
            )
            exports.update(self.detect_visual_events(asset_id, asset, original_path))
            db.mark_success(
                self.settings.stt_db_path,
                asset_id,
                wav_path=wav_path,
                duration=duration,
                diarization_stats=diarization["timing_stats"],
                raw_segments=diarization["raw_segments"],
                merged_segments=diarization["merged_segments"],
                speaker_centroids=centroids,
                transcript_segments=transcript_segments,
                exports=exports,
            )
            db.mark_partial(
                self.settings.stt_db_path,
                asset_id,
                {"type": exc.__class__.__name__, "message": str(exc)},
            )
            shutil.rmtree(work_dir, ignore_errors=True)
            return

        db.update_stage(self.settings.stt_db_path, asset_id, "writing exports")
        transcript_segments = (
            db.list_transcript_chunks(self.settings.stt_db_path, asset_id) or transcript_segments
        )
        exports = write_exports(
            self.settings.exports_dir,
            asset_id,
            asset["filename"],
            transcript_segments,
            diarization["raw_segments"],
            self.settings.parsed_export_formats,
        )
        exports.update(self.detect_visual_events(asset_id, asset, original_path))

        db.mark_success(
            self.settings.stt_db_path,
            asset_id,
            wav_path=wav_path,
            duration=duration,
            diarization_stats=diarization["timing_stats"],
            raw_segments=diarization["raw_segments"],
            merged_segments=diarization["merged_segments"],
            speaker_centroids=centroids,
            transcript_segments=transcript_segments,
            exports=exports,
        )
        shutil.rmtree(work_dir, ignore_errors=True)

    def detect_visual_events(
        self,
        asset_id: str,
        asset: dict[str, Any],
        original_path: Path,
    ) -> dict[str, str]:
        if asset.get("media_type") != "video":
            return {}

        db.update_stage(self.settings.stt_db_path, asset_id, "detecting slide changes")
        try:
            visual_events = detect_slide_changes(
                original_path,
                sample_interval_seconds=self.settings.visual_sample_interval_seconds,
                threshold=self.settings.visual_change_threshold,
                min_gap_seconds=self.settings.visual_min_gap_seconds,
            )
            db.replace_visual_events(self.settings.stt_db_path, asset_id, visual_events)
            write_visual_event_thumbnails(
                original_path,
                self.settings.exports_dir,
                asset_id,
                visual_events,
            )
            return {
                "visual_events": write_visual_events_export(
                    self.settings.exports_dir,
                    asset_id,
                    visual_events,
                )
            }
        except Exception as exc:
            db.add_event(
                self.settings.stt_db_path,
                asset_id,
                "warning",
                "detecting slide changes",
                "Slide-change detection failed",
                {"type": exc.__class__.__name__, "message": str(exc)},
            )
            return {}


def apply_speaker_names(
    transcript_segments: list[dict[str, Any]],
    speaker_matches: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    enriched = []
    for segment in transcript_segments:
        match = speaker_matches.get(segment["speaker"], {})
        enriched.append(
            {
                **segment,
                "speaker_id": match.get("speaker_id", segment["speaker"]),
                "speaker_name": match.get("display_name", segment["speaker"]),
                "speaker_similarity": match.get("score"),
            }
        )
    return enriched
