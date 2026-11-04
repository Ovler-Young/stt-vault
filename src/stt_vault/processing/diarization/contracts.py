from collections.abc import Callable
from typing import Protocol, TypedDict

import numpy as np

from stt_vault.core.models.api import JsonValue
from stt_vault.core.models.records import SpeakerSegment

type VadSegment = tuple[float, float]
type ProviderCentroids = dict[str, np.ndarray]
type ProviderTimingStats = dict[str, JsonValue]


class ProviderDiarizationPayload(TypedDict, total=False):
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


DiarizerFactory = Callable[[str], DiarizationProvider]


__all__ = [
    "DiarizationProvider",
    "DiarizerFactory",
    "ProviderCentroids",
    "ProviderDiarizationPayload",
    "ProviderTimingStats",
    "VadSegment",
]
