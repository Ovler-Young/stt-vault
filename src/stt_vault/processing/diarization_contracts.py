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
    _perform_vad: Callable[[str], list[VadSegment]]


@runtime_checkable
class SubsegmentDiarizationProvider(Protocol):
    _generate_subsegments: Callable[[list[VadSegment], bool | None], list[Subsegment]]


@runtime_checkable
class FbankDiarizationProvider(Protocol):
    _extract_fbank_features: Callable[
        [str, list[Subsegment]], tuple[FbankFeatures, Sequence[int], Sequence[int], int]
    ]


@runtime_checkable
class EmbeddingDiarizationProvider(Protocol):
    _generate_embeddings: Callable[[FbankFeatures, Sequence[int], Sequence[int], int], np.ndarray]


@runtime_checkable
class ClusteringDiarizationProvider(Protocol):
    _perform_clustering: Callable[
        [np.ndarray, list[Subsegment]],
        tuple[list[SpeakerSegment], list[SpeakerSegment], ProviderCentroids],
    ]


class InstrumentedDiarizationProvider(Protocol):
    _stt_vault_instrumented: bool


class BatchedDiarizationProvider(
    DiarizationProvider,
    VadDiarizationProvider,
    SubsegmentDiarizationProvider,
    FbankDiarizationProvider,
    EmbeddingDiarizationProvider,
    ClusteringDiarizationProvider,
    Protocol,
):
    _timing_stats: ProviderTimingStats

    def _validate_wav_file(self, wav_file: wave.Wave_read, wav_path: str) -> None: ...


DiarizerFactory = Callable[[str], DiarizationProvider]


__all__ = [
    "BatchedDiarizationProvider",
    "ClusteringDiarizationProvider",
    "DiarizationProvider",
    "DiarizerFactory",
    "EmbeddingDiarizationProvider",
    "FbankDiarizationProvider",
    "InstrumentedDiarizationProvider",
    "ProviderCentroids",
    "ProviderDiarizationPayload",
    "ProviderTimingStats",
    "Subsegment",
    "SubsegmentDiarizationProvider",
    "VadDiarizationProvider",
    "VadSegment",
]
