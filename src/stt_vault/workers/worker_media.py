from collections.abc import Callable
from pathlib import Path

from stt_vault.core.settings import Settings
from stt_vault.core.types import AssetRecord
from stt_vault.persistence import db
from stt_vault.processing.diarization import DiarizerManager
from stt_vault.processing.media import ffprobe_duration, to_wav_16k_mono

from .worker_models import PreparedAsset


class MediaPreparationStage:
    def __init__(
        self,
        settings: Settings,
        *,
        probe_duration: Callable[[Path], float] = ffprobe_duration,
        normalize_audio: Callable[[Path, Path], Path] = to_wav_16k_mono,
    ) -> None:
        self.settings = settings
        self.probe_duration = probe_duration
        self.normalize_audio = normalize_audio

    def prepare(self, asset_id: str, asset: AssetRecord) -> tuple[Path, float]:
        original_path = Path(asset["original_path"])
        wav_path = self.settings.media_dir / asset_id / "audio.16k.mono.wav"
        db.update_stage(self.settings.stt_db_path, asset_id, "probing media")
        duration = self.probe_duration(original_path)
        db.update_stage(self.settings.stt_db_path, asset_id, "normalizing audio")
        self.normalize_audio(original_path, wav_path)
        return wav_path, duration


class DiarizationStage:
    def __init__(self, settings: Settings, diarizer: DiarizerManager) -> None:
        self.settings = settings
        self.diarizer = diarizer

    def diarize(self, asset_id: str, wav_path: Path, duration: float) -> PreparedAsset:
        db.update_stage(self.settings.stt_db_path, asset_id, "identifying speakers")
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
        db.update_diarization_metadata(
            self.settings.stt_db_path,
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
