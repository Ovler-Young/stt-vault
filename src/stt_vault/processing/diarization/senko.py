import wave
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

import numpy as np

from stt_vault.core.models.records import SpeakerSegment

from .contracts import (
    BatchedDiarizationProvider,
    FbankFeatures,
    ProviderCentroids,
    ProviderDiarizationPayload,
    ProviderTimingStats,
    Subsegment,
    VadSegment,
)


@runtime_checkable
class _SenkoImplementation(Protocol):
    def diarize(
        self, wav_path: str, *, generate_colors: bool
    ) -> ProviderDiarizationPayload | None: ...

    def _validate_wav_file(self, wav_file: wave.Wave_read, wav_path: str) -> None: ...

    def _perform_vad(self, wav_path: str) -> list[VadSegment]: ...

    def _generate_subsegments(
        self, vad_segments: list[VadSegment], accurate: bool | None
    ) -> list[Subsegment]: ...

    def _extract_fbank_features(
        self, wav_path: str, subsegments: list[Subsegment]
    ) -> tuple[FbankFeatures, Sequence[int], Sequence[int], int]: ...

    def _generate_embeddings(
        self,
        features: FbankFeatures,
        frames_per_subsegment: Sequence[int],
        subsegment_offsets: Sequence[int],
        feature_dim: int,
    ) -> np.ndarray: ...

    def _perform_clustering(
        self, embeddings: np.ndarray, subsegments: list[Subsegment]
    ) -> tuple[list[SpeakerSegment], list[SpeakerSegment], ProviderCentroids]: ...


class SenkoDiarizationProvider(BatchedDiarizationProvider):
    """Expose Senko's stage operations through the application provider contract."""

    def __init__(self, implementation: object) -> None:
        if not isinstance(implementation, _SenkoImplementation):
            raise TypeError("Senko diarizer does not implement the required stage operations")
        self._implementation = implementation

    def diarize(self, wav_path: str, *, generate_colors: bool) -> ProviderDiarizationPayload | None:
        return self._implementation.diarize(wav_path, generate_colors=generate_colors)

    @property
    def timing_stats(self) -> ProviderTimingStats:
        return self._implementation._timing_stats

    @timing_stats.setter
    def timing_stats(self, value: ProviderTimingStats) -> None:
        self._implementation._timing_stats = value

    def validate_wav_file(self, wav_file: wave.Wave_read, wav_path: str) -> None:
        self._implementation._validate_wav_file(wav_file, wav_path)

    def perform_vad(self, wav_path: str) -> list[VadSegment]:
        return self._implementation._perform_vad(wav_path)

    def generate_subsegments(
        self, vad_segments: list[VadSegment], accurate: bool | None
    ) -> list[Subsegment]:
        return self._implementation._generate_subsegments(vad_segments, accurate)

    def extract_fbank_features(
        self, wav_path: str, subsegments: list[Subsegment]
    ) -> tuple[FbankFeatures, Sequence[int], Sequence[int], int]:
        return self._implementation._extract_fbank_features(wav_path, subsegments)

    def generate_embeddings(
        self,
        features: FbankFeatures,
        frames_per_subsegment: Sequence[int],
        subsegment_offsets: Sequence[int],
        feature_dim: int,
    ) -> np.ndarray:
        return self._implementation._generate_embeddings(
            features,
            frames_per_subsegment,
            subsegment_offsets,
            feature_dim,
        )

    def perform_clustering(
        self, embeddings: np.ndarray, subsegments: list[Subsegment]
    ) -> tuple[list[SpeakerSegment], list[SpeakerSegment], ProviderCentroids]:
        return self._implementation._perform_clustering(embeddings, subsegments)


__all__ = ["SenkoDiarizationProvider"]
