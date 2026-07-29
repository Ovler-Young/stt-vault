from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from stt_vault.core.config import Settings
from stt_vault.core.models.api import JsonValue
from stt_vault.core.models.records import AssetRecord, SpeakerSegment
from stt_vault.persistence.workspace.worker_repository import SqliteWorkerRepository
from stt_vault.processing.diarization import DiarizerManager
from stt_vault.processing.media_probe import ffprobe_duration
from stt_vault.processing.media_transcoding import to_wav_16k_mono

from .worker_models import PreparedAsset


class MediaRepository(Protocol):
    def update_stage(self, asset_id: str, stage: str) -> None: ...

    def update_diarization_metadata(
        self,
        asset_id: str,
        *,
        wav_path: Path,
        duration: float,
        diarization_stats: dict[str, JsonValue],
        raw_segments: list[SpeakerSegment],
        merged_segments: list[SpeakerSegment],
        speaker_centroids: dict[str, list[float]],
    ) -> None: ...


class MediaPreparationStage:
    def __init__(
        self,
        settings: Settings,
        *,
        probe_duration: Callable[[Path], float] = ffprobe_duration,
        normalize_audio: Callable[[Path, Path], Path] = to_wav_16k_mono,
        repository: MediaRepository | None = None,
    ) -> None:
        self.settings = settings
        self.probe_duration = probe_duration
        self.normalize_audio = normalize_audio
        self.repository = repository or SqliteWorkerRepository(settings.stt_db_path)

    def prepare(self, asset_id: str, asset: AssetRecord) -> tuple[Path, float]:
        original_path = Path(asset["original_path"])
        wav_path = self.settings.media_dir / asset_id / "audio.16k.mono.wav"
        self.repository.update_stage(asset_id, "probing media")
        duration = self.probe_duration(original_path)
        self.repository.update_stage(asset_id, "normalizing audio")
        self.normalize_audio(original_path, wav_path)
        return wav_path, duration


class DiarizationStage:
    def __init__(
        self,
        settings: Settings,
        diarizer: DiarizerManager,
        *,
        repository: MediaRepository | None = None,
    ) -> None:
        self.settings = settings
        self.diarizer = diarizer
        self.repository = repository or SqliteWorkerRepository(settings.stt_db_path)

    def diarize(self, asset_id: str, wav_path: Path, duration: float) -> PreparedAsset:
        self.repository.update_stage(asset_id, "identifying speakers")
        result = self.diarizer.diarize(str(wav_path))
        if result is None:
            raise RuntimeError("No speech detected")
        raw_segments = [
            {"start": segment.start, "end": segment.end, "speaker": segment.speaker}
            for segment in result.raw_segments
        ]
        merged_segments = [
            {"start": segment.start, "end": segment.end, "speaker": segment.speaker}
            for segment in result.merged_segments
        ]
        self.repository.update_diarization_metadata(
            asset_id,
            wav_path=wav_path,
            duration=duration,
            diarization_stats=result.timing_stats,
            raw_segments=raw_segments,
            merged_segments=merged_segments,
            speaker_centroids=result.speaker_centroids,
        )
        return PreparedAsset(
            wav_path=wav_path,
            duration=duration,
            diarization_stats=result.timing_stats,
            raw_segments=raw_segments,
            merged_segments=merged_segments,
            speaker_centroids=result.speaker_centroids,
        )
