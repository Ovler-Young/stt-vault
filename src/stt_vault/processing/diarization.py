import threading
import time
from collections.abc import Mapping
from typing import cast

import numpy as np

from stt_vault.core.models.api import DiarizationResult, JsonValue
from stt_vault.core.models.records import KnownSpeaker, SpeakerMatch
from stt_vault.processing.diarization_contracts import (
    BatchedDiarizationProvider,
    DiarizationProvider,
    DiarizerFactory,
    ProviderCentroids,
)
from stt_vault.processing.diarization_instrumentation import current_rss_mb, instrument_diarizer
from stt_vault.processing.diarization_pipeline import run_batched_diarization


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
                provider_result = run_batched_diarization(
                    cast(BatchedDiarizationProvider, diarizer),
                    wav_path,
                    fbank_batch_segments=self.fbank_batch_segments,
                    generate_colors=True,
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
            instrument_diarizer(self._diarizer, self._record_stage_resource)
            self._last_used = time.monotonic()
            self._resource_stats["load_diarizer"] = {
                "wall_time": round(time.perf_counter() - start, 3),
                "rss_mb_before": rss_before,
                "rss_mb_after": current_rss_mb(),
            }
        return self._diarizer

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
