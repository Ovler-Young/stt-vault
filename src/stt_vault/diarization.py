import threading
import time
from typing import Any

import numpy as np


class DiarizerManager:
    def __init__(
        self,
        *,
        device: str,
        idle_timeout_seconds: int,
        use_batched_embeddings: bool = False,
    ) -> None:
        self.device = device
        self.idle_timeout_seconds = idle_timeout_seconds
        self.use_batched_embeddings = use_batched_embeddings
        self._lock = threading.Lock()
        self._diarizer = None
        self._last_used = 0.0

    def diarize(self, wav_path: str) -> dict[str, Any] | None:
        with self._lock:
            diarizer = self._get_or_create()
            if self.use_batched_embeddings:
                # Senko does not expose a batched fbank->embedding accumulation path yet.
                # Keeping this branch here prevents the rest of the service from depending
                # on Senko internals when that upstream path is added.
                result = diarizer.diarize(wav_path, generate_colors=True)
            else:
                result = diarizer.diarize(wav_path, generate_colors=True)
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

            self._diarizer = Diarizer(device=self.device, warmup=True, quiet=False)
            self._last_used = time.monotonic()
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

