import threading
import time
from collections.abc import Mapping
from typing import cast

import numpy as np

from stt_vault.core.models.api import DiarizationResult, JsonValue
from stt_vault.core.models.mod_contracts import EmbeddingSpaceV1
from stt_vault.core.models.records import SpeakerMatch, SpeakerRecord

from .contracts import DiarizationProvider, DiarizerFactory, SenkoDiarizationProvider
from .instrumentation import current_rss_mb


def _create_senko_diarizer(device: str, embedding_space: EmbeddingSpaceV1) -> DiarizationProvider:
    if device in {"auto", "cpu"}:
        import torch

        if device == "cpu" or not torch.cuda.is_available():
            torch.backends.nnpack.set_flags(False)
    from senko import Diarizer

    return SenkoDiarizationProvider(
        Diarizer(device=device, warmup=True, quiet=True),
        embedding_space,
    )


class DiarizerManager:
    def __init__(
        self,
        *,
        device: str,
        idle_timeout_seconds: int,
        embedding_space: EmbeddingSpaceV1,
        diarizer_factory: DiarizerFactory = _create_senko_diarizer,
    ) -> None:
        self.device = device
        self.idle_timeout_seconds = idle_timeout_seconds
        self.embedding_space = embedding_space
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
            provider_result = diarizer.diarize(wav_path, generate_colors=True)
            elapsed = time.perf_counter() - start
            if provider_result is not None:
                result = _validate_provider_result(provider_result)
                timing_stats = dict(result.timing_stats)
                timing_stats["manager_diarize_wall_time"] = round(elapsed, 3)
                timing_stats["manager_rss_mb_before"] = rss_before
                timing_stats["manager_rss_mb_after"] = current_rss_mb()
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
            self._diarizer = self.diarizer_factory(self.device, self.embedding_space)
            self._last_used = time.monotonic()
            self._resource_stats["load_diarizer"] = {
                "wall_time": round(time.perf_counter() - start, 3),
                "rss_mb_before": rss_before,
                "rss_mb_after": current_rss_mb(),
            }
        return self._diarizer


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
    known_speakers: list[SpeakerRecord],
    threshold: float,
    *,
    embedding_space: EmbeddingSpaceV1 | Mapping[str, object] | None,
) -> dict[str, SpeakerMatch]:
    matches: dict[str, SpeakerMatch] = {}
    asset_embedding_space = _cosine_embedding_space(embedding_space)
    for local_speaker, centroid in centroids.items():
        best: SpeakerMatch | None = None
        if not _centroid_matches_embedding_space(centroid, asset_embedding_space):
            matches[local_speaker] = SpeakerMatch(local_speaker, local_speaker, None)
            continue
        for known in known_speakers:
            if asset_embedding_space is None:
                continue
            known_embedding_space = _cosine_embedding_space(known.embedding_space)
            if known_embedding_space != asset_embedding_space:
                continue
            if not _centroid_matches_embedding_space(known.centroid, known_embedding_space):
                continue
            score = cosine_similarity(centroid, list(known.centroid))
            best_score = best.score if best and best.score is not None else float("-inf")
            if best is None or score > best_score:
                best = SpeakerMatch(known.id, known.display_name, score)
        if best is not None and best.score is not None and best.score >= threshold:
            matches[local_speaker] = best
        else:
            matches[local_speaker] = SpeakerMatch(local_speaker, local_speaker, None)
    return matches


def _cosine_embedding_space(value: object) -> EmbeddingSpaceV1 | None:
    if isinstance(value, EmbeddingSpaceV1):
        return value
    if not isinstance(value, Mapping):
        return None
    try:
        return EmbeddingSpaceV1.model_validate(value)
    except ValueError:
        return None


def _validated_provider_centroids(value: object) -> dict[str, np.ndarray]:
    if not isinstance(value, Mapping):
        raise ValueError("Diarization provider returned invalid speaker centroids")
    centroids: dict[str, np.ndarray] = {}
    for speaker, centroid in value.items():
        if not isinstance(speaker, str) or not isinstance(centroid, np.ndarray):
            raise ValueError("Diarization provider returned invalid speaker centroid")
        if centroid.ndim != 1 or not np.issubdtype(centroid.dtype, np.number):
            raise ValueError("Diarization provider returned invalid speaker centroid")
        centroids[speaker] = centroid
    return centroids


def validate_centroids_for_embedding_space(
    centroids: Mapping[str, object],
    embedding_space: EmbeddingSpaceV1 | Mapping[str, object] | None,
) -> None:
    if not centroids:
        return
    if embedding_space is None:
        raise ValueError("Diarization result has centroids without an embedding space")
    try:
        space = (
            embedding_space
            if isinstance(embedding_space, EmbeddingSpaceV1)
            else EmbeddingSpaceV1.model_validate(embedding_space)
        )
    except ValueError as error:
        raise ValueError("Diarization result has an invalid embedding space") from error
    for centroid in centroids.values():
        vector = np.asarray(centroid)
        if vector.ndim != 1 or not np.issubdtype(vector.dtype, np.number):
            raise ValueError("Diarization result has an invalid speaker centroid")
        if len(vector) != space.dimension:
            raise ValueError("Diarization centroid dimension does not match its embedding space")
        if not np.all(np.isfinite(vector)):
            raise ValueError("Diarization centroid values must be finite")
        if not np.any(vector != 0):
            raise ValueError("Diarization centroid norm must be nonzero")


def _centroid_matches_embedding_space(
    centroid: object,
    embedding_space: EmbeddingSpaceV1 | None,
) -> bool:
    try:
        validate_centroids_for_embedding_space({"centroid": centroid}, embedding_space)
    except (TypeError, ValueError):
        return False
    return True


def _validate_provider_result(provider_result: object) -> DiarizationResult:
    if not isinstance(provider_result, Mapping):
        raise ValueError("Diarization provider returned a non-object result")
    typed_centroids = _validated_provider_centroids(provider_result.get("speaker_centroids", {}))
    embedding_space = provider_result.get("embedding_space")
    try:
        validate_centroids_for_embedding_space(typed_centroids, embedding_space)
    except ValueError as error:
        raise ValueError(f"Diarization provider returned {error.args[0].lower()}") from error
    return DiarizationResult.model_validate(
        {
            "raw_segments": provider_result.get("raw_segments"),
            "merged_segments": provider_result.get("merged_segments"),
            "speaker_centroids": serialize_centroids(typed_centroids),
            "timing_stats": provider_result.get("timing_stats"),
            "embedding_space": embedding_space,
        }
    )
