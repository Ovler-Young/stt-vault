import logging
import threading
import time
import wave
from collections.abc import Callable, Mapping, Sequence
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, ParamSpec, Protocol, TypedDict, TypeVar, cast, runtime_checkable

import numpy as np

from stt_vault.core.models.api import DiarizationResult, JsonValue
from stt_vault.core.models.records import KnownSpeaker, SpeakerMatch, SpeakerSegment

if TYPE_CHECKING:
    import torch

P = ParamSpec("P")
R = TypeVar("R")
logger = logging.getLogger(__name__)


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


def _create_senko_diarizer(device: str) -> DiarizationProvider:
    from senko import Diarizer

    return cast(DiarizationProvider, cast(object, Diarizer(device=device, warmup=True, quiet=True)))


class DiarizerManager:
    def __init__(
        self,
        *,
        device: str,
        idle_timeout_seconds: int,
        use_batched_embeddings: bool = False,
        fbank_batch_segments: int = 256,
        diarizer_factory: DiarizerFactory = _create_senko_diarizer,
    ) -> None:
        self.device = device
        self.idle_timeout_seconds = idle_timeout_seconds
        self.use_batched_embeddings = use_batched_embeddings
        self.fbank_batch_segments = max(1, fbank_batch_segments)
        self.diarizer_factory = diarizer_factory
        self._lock = threading.Lock()
        self._diarizer: DiarizationProvider | None = None
        self._last_used = 0.0
        self._resource_stats: dict[str, dict[str, float | int | None]] = {}

    def diarize(self, wav_path: str) -> DiarizationResult | None:
        with self._lock:
            self._resource_stats = {}
            diarizer = self._get_or_create()
            result: DiarizationResult | None = None
            rss_before = current_rss_mb()
            start = time.perf_counter()
            if self.use_batched_embeddings:
                provider_result = self._diarize_batched(
                    cast(BatchedDiarizationProvider, diarizer), wav_path, generate_colors=True
                )
            else:
                provider_result = diarizer.diarize(wav_path, generate_colors=True)
            elapsed = time.perf_counter() - start
            if provider_result is not None:
                result = _validate_provider_result(provider_result)
                timing_stats = dict(result.timing_stats)
                timing_stats["manager_diarize_wall_time"] = round(elapsed, 3)
                timing_stats["manager_rss_mb_before"] = rss_before
                timing_stats["manager_rss_mb_after"] = current_rss_mb()
                timing_stats["senko_batched_embeddings_requested"] = self.use_batched_embeddings
                timing_stats["senko_fbank_batch_segments"] = self.fbank_batch_segments
                timing_stats["senko_resource_stats"] = cast(JsonValue, self._resource_stats)
                result = result.model_copy(update={"timing_stats": timing_stats})
            self._last_used = time.monotonic()
            return result

    def maybe_unload(self) -> None:
        with self._lock:
            if self._diarizer is None:
                return
            idle_for = time.monotonic() - self._last_used
            if idle_for >= self.idle_timeout_seconds:
                self._diarizer = None

    def _get_or_create(self) -> DiarizationProvider:
        if self._diarizer is None:
            rss_before = current_rss_mb()
            start = time.perf_counter()
            self._diarizer = self.diarizer_factory(self.device)
            self._instrument_diarizer(self._diarizer)
            self._last_used = time.monotonic()
            self._resource_stats["load_diarizer"] = {
                "wall_time": round(time.perf_counter() - start, 3),
                "rss_mb_before": rss_before,
                "rss_mb_after": current_rss_mb(),
            }
        return self._diarizer

    def _diarize_batched(
        self,
        diarizer: BatchedDiarizationProvider,
        wav_path: str,
        *,
        accurate: bool | None = None,
        generate_colors: bool = False,
    ) -> ProviderDiarizationPayload | None:
        diarizer._timing_stats = {}
        total_start = time.time()

        logger.info(
            "diarization started",
            extra={"event_name": "diarization.started", "media_filename": Path(wav_path).name},
        )
        with wave.open(wav_path, "rb") as wav_file:
            diarizer._validate_wav_file(wav_file, wav_path)

        vad_segments = diarizer._perform_vad(wav_path)
        if not vad_segments:
            return None

        subsegments = diarizer._generate_subsegments(vad_segments, accurate)
        embeddings_batches = []
        for start in range(0, len(subsegments), self.fbank_batch_segments):
            batch_subsegments = subsegments[start : start + self.fbank_batch_segments]
            features_flat, frames_per_subsegment, subsegment_offsets, feature_dim = (
                diarizer._extract_fbank_features(wav_path, batch_subsegments)
            )
            subsegment_offsets = [int(offset) for offset in subsegment_offsets]
            embeddings_batches.append(
                diarizer._generate_embeddings(
                    features_flat,
                    frames_per_subsegment,
                    subsegment_offsets,
                    feature_dim,
                )
            )

        embeddings = np.concatenate(embeddings_batches, axis=0)
        raw_segments, merged_segments, centroids = diarizer._perform_clustering(
            embeddings,
            subsegments,
        )

        total_time = round(time.time() - total_start, 2)
        diarizer._timing_stats["total_time"] = total_time
        raw_speakers_detected = len({segment["speaker"] for segment in raw_segments})
        merged_speakers_detected = len({segment["speaker"] for segment in merged_segments})

        result = {
            "raw_segments": raw_segments,
            "raw_speakers_detected": raw_speakers_detected,
            "merged_speakers_detected": merged_speakers_detected,
            "merged_segments": merged_segments,
            "speaker_centroids": centroids,
            "timing_stats": diarizer._timing_stats,
            "vad": vad_segments,
        }

        if generate_colors:
            from senko.colors import generate_speaker_colors

            result["speaker_color_sets"] = {
                str(index): generate_speaker_colors(merged_segments, index) for index in range(10)
            }

        return cast(ProviderDiarizationPayload, cast(object, result))

    def _instrument_diarizer(self, diarizer: DiarizationProvider) -> None:
        instrumented_diarizer = cast(InstrumentedDiarizationProvider, cast(object, diarizer))
        if getattr(instrumented_diarizer, "_stt_vault_instrumented", False):
            return

        if isinstance(diarizer, VadDiarizationProvider):
            diarizer._perform_vad = self._wrap_stage("vad", diarizer._perform_vad)
        if isinstance(diarizer, SubsegmentDiarizationProvider):
            diarizer._generate_subsegments = self._wrap_stage(
                "subsegments", diarizer._generate_subsegments
            )
        if isinstance(diarizer, FbankDiarizationProvider):
            diarizer._extract_fbank_features = self._wrap_stage(
                "fbank", diarizer._extract_fbank_features
            )
        if isinstance(diarizer, EmbeddingDiarizationProvider):
            diarizer._generate_embeddings = self._wrap_stage(
                "embeddings", diarizer._generate_embeddings
            )
        if isinstance(diarizer, ClusteringDiarizationProvider):
            diarizer._perform_clustering = self._wrap_stage(
                "clustering", diarizer._perform_clustering
            )
        instrumented_diarizer._stt_vault_instrumented = True

    def _wrap_stage(self, stage_name: str, method: Callable[P, R]) -> Callable[P, R]:
        @wraps(method)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            rss_before = current_rss_mb()
            start = time.perf_counter()
            try:
                return method(*args, **kwargs)
            finally:
                self._record_stage_resource(stage_name, time.perf_counter() - start, rss_before)

        return wrapped

    def _record_stage_resource(
        self,
        stage_name: str,
        elapsed: float,
        rss_before: float | None,
    ) -> None:
        rss_after = current_rss_mb()
        stats = self._resource_stats.setdefault(
            stage_name,
            {
                "calls": 0,
                "wall_time": 0.0,
                "rss_mb_before": rss_before,
                "rss_mb_after": rss_after,
                "rss_mb_peak": rss_after,
            },
        )
        stats["calls"] = int(stats["calls"] or 0) + 1
        stats["wall_time"] = round(float(stats["wall_time"] or 0.0) + elapsed, 3)
        stats["rss_mb_after"] = rss_after
        if rss_before is not None:
            before = stats.get("rss_mb_before")
            stats["rss_mb_before"] = (
                rss_before if before is None else min(float(before), rss_before)
            )
        if rss_after is not None:
            peak = stats.get("rss_mb_peak")
            stats["rss_mb_peak"] = rss_after if peak is None else max(float(peak), rss_after)


def current_rss_mb() -> float | None:
    try:
        import resource

        value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except Exception:
        return None

    # Linux reports KiB; macOS reports bytes.
    if value > 1024 * 1024:
        value = value / (1024 * 1024)
    else:
        value = value / 1024
    return round(value, 1)


def serialize_centroids(centroids: dict[str, np.ndarray]) -> dict[str, list[float]]:
    return {speaker: centroid.astype(float).tolist() for speaker, centroid in centroids.items()}


def cosine_similarity(left: list[float], right: list[float]) -> float:
    left_array = np.asarray(left, dtype=np.float32)
    right_array = np.asarray(right, dtype=np.float32)
    denominator = np.linalg.norm(left_array) * np.linalg.norm(right_array)
    if denominator == 0:
        return 0.0
    return float(np.dot(left_array, right_array) / denominator)


def match_speakers(
    centroids: dict[str, list[float]],
    known_speakers: list[KnownSpeaker],
    threshold: float,
) -> dict[str, SpeakerMatch]:
    matches: dict[str, SpeakerMatch] = {}
    for local_speaker, centroid in centroids.items():
        best: SpeakerMatch | None = None
        for known in known_speakers:
            score = cosine_similarity(centroid, known["centroid"])
            best_score = best["score"] if best and best["score"] is not None else float("-inf")
            if best is None or score > best_score:
                best = {
                    "speaker_id": known["id"],
                    "display_name": known["display_name"],
                    "score": score,
                }
        if best is not None and best["score"] is not None and best["score"] >= threshold:
            matches[local_speaker] = best
        else:
            matches[local_speaker] = {
                "speaker_id": local_speaker,
                "display_name": local_speaker,
                "score": None,
            }
    return matches


def _validate_provider_result(provider_result: object) -> DiarizationResult:
    if not isinstance(provider_result, Mapping):
        raise ValueError("Diarization provider returned a non-object result")
    centroids = provider_result.get("speaker_centroids", {})
    if not isinstance(centroids, dict):
        raise ValueError("Diarization provider returned invalid speaker centroids")
    typed_centroids = cast(ProviderCentroids, cast(object, centroids))
    return DiarizationResult.model_validate(
        {
            "raw_segments": provider_result.get("raw_segments"),
            "merged_segments": provider_result.get("merged_segments"),
            "speaker_centroids": serialize_centroids(typed_centroids),
            "timing_stats": provider_result.get("timing_stats"),
        }
    )
