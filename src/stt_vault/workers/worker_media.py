import hashlib
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from stt_vault.core.config import Settings
from stt_vault.core.models.records import (
    AssetRecord,
    CompleteDiarizationProviderInvocation,
    DiarizationMetadata,
    PrepareProviderWorkItem,
    ProviderInvocationTransition,
    SpeakerSegment,
)
from stt_vault.persistence.sqlite_database import SqliteDatabase
from stt_vault.processing.diarization import DiarizerManager, validate_centroids_for_embedding_space
from stt_vault.processing.media_probe import ffprobe_duration
from stt_vault.processing.media_transcoding import to_wav_16k_mono

from .worker_models import PreparedAsset


class MediaPreparationStage:
    def __init__(
        self,
        settings: Settings,
        database: SqliteDatabase,
        probe_duration: Callable[[Path], float] = ffprobe_duration,
        normalize_audio: Callable[[Path, Path], Path] = to_wav_16k_mono,
    ) -> None:
        self.settings = settings
        self.probe_duration = probe_duration
        self.normalize_audio = normalize_audio
        self.database = database

    def prepare(self, asset_id: str, asset: AssetRecord) -> tuple[Path, float]:
        original_path = Path(asset.original_path)
        wav_path = self.settings.media_dir / asset_id / "audio.16k.mono.wav"
        self.database.update_stage(asset_id=asset_id, stage="probing media")
        duration = self.probe_duration(original_path)
        self.database.update_stage(asset_id=asset_id, stage="normalizing audio")
        self.normalize_audio(original_path, wav_path)
        return wav_path, duration


class DiarizationStage:
    def __init__(
        self,
        settings: Settings,
        diarizer: DiarizerManager,
        database: SqliteDatabase,
    ) -> None:
        self.settings = settings
        self.diarizer = diarizer
        self.database = database

    def diarize(
        self,
        asset_id: str,
        wav_path: Path,
        duration: float,
        *,
        job_id: str | None = None,
        run_attempt: int | None = None,
        work_generation: int = 1,
    ) -> PreparedAsset:
        self.database.update_stage(asset_id=asset_id, stage="identifying speakers")
        result = self.diarizer.diarize(str(wav_path))
        if result is None:
            raise RuntimeError("No speech detected")
        validate_centroids_for_embedding_space(result.speaker_centroids, result.embedding_space)
        raw_segments = [
            SpeakerSegment(segment.start, segment.end, segment.speaker)
            for segment in result.raw_segments
        ]
        merged_segments = [
            SpeakerSegment(segment.start, segment.end, segment.speaker)
            for segment in result.merged_segments
        ]
        metadata = DiarizationMetadata(
            asset_id,
            wav_path,
            duration,
            result.timing_stats,
            raw_segments,
            merged_segments,
            result.speaker_centroids,
            result.embedding_space,
        )
        if job_id is None or run_attempt is None:
            self.database.update_diarization_metadata(metadata)
        else:
            request_hash = hashlib.sha256(
                f"{asset_id}:{duration}:{work_generation}".encode()
            ).hexdigest()
            invocation = self.database.prepare_provider_work_item(
                PrepareProviderWorkItem(
                    uuid4().hex,
                    job_id,
                    asset_id,
                    "diarization",
                    "asset",
                    run_attempt,
                    str(uuid4()),
                    request_hash,
                    "senko",
                    "local",
                    work_generation,
                    str(uuid4()),
                )
            )
            for transition in (
                ProviderInvocationTransition.sent(invocation),
                ProviderInvocationTransition.accepted(invocation),
            ):
                if not self.database.transition_provider_invocation(transition).applied:
                    raise RuntimeError("diarization invocation transition was stale")
            completion = CompleteDiarizationProviderInvocation(
                invocation.work_item_id,
                invocation.invocation_attempt,
                invocation.run_attempt,
                asset_id,
                metadata,
            )
            if not self.database.complete_diarization_and_provider_invocation(completion).applied:
                raise RuntimeError("diarization invocation completion was stale")
        return PreparedAsset(
            wav_path=wav_path,
            duration=duration,
            diarization_stats=result.timing_stats,
            raw_segments=raw_segments,
            merged_segments=merged_segments,
            speaker_centroids=result.speaker_centroids,
            embedding_space=result.embedding_space,
        )
