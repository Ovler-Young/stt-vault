from collections.abc import Callable, Mapping
from typing import Protocol, TypedDict

import numpy as np

from stt_vault.core.models.api import JsonValue
from stt_vault.core.models.mod_contracts import EmbeddingSpaceV1
from stt_vault.core.models.records import SpeakerSegment

type VadSegment = tuple[float, float]
type ProviderCentroids = dict[str, np.ndarray]
type ProviderTimingStats = dict[str, JsonValue]


class ProviderDiarizationPayload(TypedDict, total=False):
    embedding_space: EmbeddingSpaceV1
    raw_segments: list[SpeakerSegment]
    raw_speakers_detected: int
    merged_speakers_detected: int
    merged_segments: list[SpeakerSegment]
    speaker_centroids: ProviderCentroids
    timing_stats: ProviderTimingStats
    vad: list[VadSegment]
    speaker_color_sets: dict[str, dict[str, str]]


class DiarizationProvider(Protocol):
    def diarize(
        self, wav_path: str, *, generate_colors: bool
    ) -> ProviderDiarizationPayload | None: ...


DiarizerFactory = Callable[[str, EmbeddingSpaceV1], DiarizationProvider]


class SenkoDiarizationProvider:
    def __init__(self, diarizer: DiarizationProvider, embedding_space: EmbeddingSpaceV1) -> None:
        self.diarizer = diarizer
        self.embedding_space = embedding_space

    def diarize(self, wav_path: str, *, generate_colors: bool) -> ProviderDiarizationPayload | None:
        result = self.diarizer.diarize(wav_path, generate_colors=generate_colors)
        if result is None:
            return None
        if not isinstance(result, Mapping):
            return result
        return {**result, "embedding_space": self.embedding_space}


__all__ = [
    "DiarizationProvider",
    "DiarizerFactory",
    "ProviderCentroids",
    "ProviderDiarizationPayload",
    "ProviderTimingStats",
    "SenkoDiarizationProvider",
    "VadSegment",
]
