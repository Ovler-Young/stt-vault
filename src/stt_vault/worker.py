import shutil
import threading
import time
from pathlib import Path
from typing import Any

from . import db
from .diarization import DiarizerManager, match_speakers, serialize_centroids
from .exports import write_exports
from .media import ffprobe_duration, to_wav_16k_mono
from .settings import Settings
from .transcription import Transcriber, build_chunks


class Worker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name="stt-vault-worker", daemon=True)
        self.diarizer = DiarizerManager(
            device=settings.senko_device,
            idle_timeout_seconds=settings.diarizer_idle_timeout_seconds,
            use_batched_embeddings=settings.senko_batched_embeddings,
        )

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=10)

    def run(self) -> None:
        while not self.stop_event.is_set():
            asset_id = db.claim_next_job(self.settings.stt_db_path)
            if asset_id is None:
                self.diarizer.maybe_unload()
                self.stop_event.wait(2)
                continue

            try:
                self.process(asset_id)
            except Exception as exc:
                db.mark_failed(
                    self.settings.stt_db_path,
                    asset_id,
                    {"type": exc.__class__.__name__, "message": str(exc)},
                )

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
        known_speakers = db.list_speakers(self.settings.stt_db_path)
        speaker_matches = match_speakers(
            centroids,
            known_speakers,
            self.settings.speaker_similarity_threshold,
        )

        db.update_stage(self.settings.stt_db_path, asset_id, "transcribing speech")
        transcriber = Transcriber(
            api_key=self.settings.openai_api_key,
            base_url=self.settings.openai_base_url,
            model=self.settings.openai_transcribe_model,
            prompt=self.settings.openai_transcribe_prompt,
            concurrency=self.settings.openai_concurrency,
        )
        chunks = build_chunks(
            diarization["merged_segments"],
            max_seconds=self.settings.transcribe_chunk_seconds,
            overlap_seconds=self.settings.transcribe_chunk_overlap_seconds,
        )
        transcript_segments = transcriber.transcribe_chunks(wav_path, chunks, work_dir)
        transcript_segments = apply_speaker_names(transcript_segments, speaker_matches)

        db.update_stage(self.settings.stt_db_path, asset_id, "writing exports")
        exports = write_exports(
            self.settings.exports_dir,
            asset_id,
            asset["filename"],
            transcript_segments,
            diarization["raw_segments"],
            self.settings.parsed_export_formats,
        )

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

