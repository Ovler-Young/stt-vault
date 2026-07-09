import threading
import time
import wave
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any

import numpy as np


class DiarizerManager:
    def __init__(
        self,
        *,
        device: str,
        idle_timeout_seconds: int,
        use_batched_embeddings: bool = False,
        fbank_batch_segments: int = 256,
    ) -> None:
        self.device = device
        self.idle_timeout_seconds = idle_timeout_seconds
        self.use_batched_embeddings = use_batched_embeddings
        self.fbank_batch_segments = max(1, fbank_batch_segments)
        self._lock = threading.Lock()
        self._diarizer = None
        self._last_used = 0.0
        self._resource_stats: dict[str, dict[str, float | int | None]] = {}

    def diarize(self, wav_path: str) -> dict[str, Any] | None:
        with self._lock:
            self._resource_stats = {}
            diarizer = self._get_or_create()
            rss_before = current_rss_mb()
            start = time.perf_counter()
            if self.use_batched_embeddings:
                result = self._diarize_batched(diarizer, wav_path, generate_colors=True)
            else:
                result = diarizer.diarize(wav_path, generate_colors=True)
            elapsed = time.perf_counter() - start
            if result is not None:
                timing_stats = result.setdefault("timing_stats", {})
                timing_stats["manager_diarize_wall_time"] = round(elapsed, 3)
                timing_stats["manager_rss_mb_before"] = rss_before
                timing_stats["manager_rss_mb_after"] = current_rss_mb()
                timing_stats["senko_batched_embeddings_requested"] = self.use_batched_embeddings
                timing_stats["senko_fbank_batch_segments"] = self.fbank_batch_segments
                timing_stats["senko_resource_stats"] = self._resource_stats
            self._last_used = time.monotonic()
            return result

    def maybe_unload(self) -> None:
        with self._lock:
            if self._diarizer is None:
                return
            idle_for = time.monotonic() - self._last_used
            if idle_for >= self.idle_timeout_seconds:
                self._diarizer = None

    def _get_or_create(self):
        if self._diarizer is None:
            from senko import Diarizer

            rss_before = current_rss_mb()
            start = time.perf_counter()
            self._diarizer = Diarizer(device=self.device, warmup=True, quiet=False)
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
        diarizer: Any,
        wav_path: str,
        *,
        accurate: bool | None = None,
        generate_colors: bool = False,
    ) -> dict[str, Any] | None:
        diarizer._timing_stats = {}
        total_start = time.time()

        diarizer._print(f"\n    \033[38;2;120;167;214m{Path(wav_path).name}\033[0m")
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
                str(index): generate_speaker_colors(merged_segments, index)
                for index in range(10)
            }

        return result

    def _instrument_diarizer(self, diarizer: Any) -> None:
        if getattr(diarizer, "_stt_vault_instrumented", False):
            return

        stages = {
            "_perform_vad": "vad",
            "_generate_subsegments": "subsegments",
            "_extract_fbank_features": "fbank",
            "_generate_embeddings": "embeddings",
            "_perform_clustering": "clustering",
        }
        for method_name, stage_name in stages.items():
            method = getattr(diarizer, method_name, None)
            if method is None:
                continue
            setattr(diarizer, method_name, self._wrap_stage(stage_name, method))
        diarizer._stt_vault_instrumented = True

    def _wrap_stage(self, stage_name: str, method: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(method)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
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
    known_speakers: list[dict[str, Any]],
    threshold: float,
) -> dict[str, dict[str, Any]]:
    matches: dict[str, dict[str, Any]] = {}
    for local_speaker, centroid in centroids.items():
        best = None
        for known in known_speakers:
            score = cosine_similarity(centroid, known["centroid"])
            if best is None or score > best["score"]:
                best = {
                    "speaker_id": known["id"],
                    "display_name": known["display_name"],
                    "score": score,
                }
        if best and best["score"] >= threshold:
            matches[local_speaker] = best
        else:
            matches[local_speaker] = {
                "speaker_id": local_speaker,
                "display_name": local_speaker,
                "score": None,
            }
    return matches
