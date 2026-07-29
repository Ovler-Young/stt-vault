import wave
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Protocol, TypedDict, runtime_checkable

import numpy as np

from stt_vault.core.models.api import JsonValue
from stt_vault.core.models.records import SpeakerSegment

if TYPE_CHECKING:
    import torch


type VadSegment = tuple[float, float]
type Subsegment = tuple[float, float]
if TYPE_CHECKING:
    type FbankFeatures = np.ndarray | torch.Tensor
else:
    type FbankFeatures = np.ndarray
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


@runtime_checkable
class VadDiarizationProvider(Protocol):
    def perform_vad(self, wav_path: str) -> list[VadSegment]: ...


@runtime_checkable
class SubsegmentDiarizationProvider(Protocol):
    def generate_subsegments(
        self, vad_segments: list[VadSegment], accurate: bool | None
    ) -> list[Subsegment]: ...


@runtime_checkable
class FbankDiarizationProvider(Protocol):
    def extract_fbank_features(
        self, wav_path: str, subsegments: list[Subsegment]
    ) -> tuple[FbankFeatures, Sequence[int], Sequence[int], int]: ...


@runtime_checkable
class EmbeddingDiarizationProvider(Protocol):
    def generate_embeddings(
        self,
        features: FbankFeatures,
        frames_per_subsegment: Sequence[int],
        subsegment_offsets: Sequence[int],
        feature_dim: int,
    ) -> np.ndarray: ...


@runtime_checkable
class ClusteringDiarizationProvider(Protocol):
    def perform_clustering(
        self, embeddings: np.ndarray, subsegments: list[Subsegment]
    ) -> tuple[list[SpeakerSegment], list[SpeakerSegment], ProviderCentroids]: ...


class BatchedDiarizationProvider(
    DiarizationProvider,
    VadDiarizationProvider,
    SubsegmentDiarizationProvider,
    FbankDiarizationProvider,
    EmbeddingDiarizationProvider,
    ClusteringDiarizationProvider,
    Protocol,
):
    @property
    def timing_stats(self) -> ProviderTimingStats: ...

    @timing_stats.setter
    def timing_stats(self, value: ProviderTimingStats) -> None: ...

    def validate_wav_file(self, wav_file: wave.Wave_read, wav_path: str) -> None: ...


DiarizerFactory = Callable[[str], DiarizationProvider]


__all__ = [
    "BatchedDiarizationProvider",
    "ClusteringDiarizationProvider",
    "DiarizationProvider",
    "DiarizerFactory",
    "EmbeddingDiarizationProvider",
    "FbankDiarizationProvider",
    "ProviderCentroids",
    "ProviderDiarizationPayload",
    "ProviderTimingStats",
    "Subsegment",
    "SubsegmentDiarizationProvider",
    "VadDiarizationProvider",
    "VadSegment",
]
