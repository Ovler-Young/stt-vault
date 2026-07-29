import logging
import time
import wave
from collections.abc import Callable
from pathlib import Path
from typing import cast

import numpy as np

from .contracts import (
    BatchedDiarizationProvider,
    ProviderDiarizationPayload,
)

logger = logging.getLogger(__name__)
Clock = Callable[[], float]


def run_batched_diarization(
    diarizer: BatchedDiarizationProvider,
    wav_path: str,
    *,
    fbank_batch_segments: int,
    accurate: bool | None = None,
    generate_colors: bool = False,
    clock: Clock = time.time,
) -> ProviderDiarizationPayload | None:
    diarizer.timing_stats = {}
    total_start = clock()

    logger.info(
        "diarization started",
        extra={"event_name": "diarization.started", "media_filename": Path(wav_path).name},
    )
    with wave.open(wav_path, "rb") as wav_file:
        diarizer.validate_wav_file(wav_file, wav_path)

    vad_segments = diarizer.perform_vad(wav_path)
    if not vad_segments:
        return None

    subsegments = diarizer.generate_subsegments(vad_segments, accurate)
    embeddings_batches: list[np.ndarray] = []
    for start in range(0, len(subsegments), fbank_batch_segments):
        batch_subsegments = subsegments[start : start + fbank_batch_segments]
        features_flat, frames_per_subsegment, subsegment_offsets, feature_dim = (
            diarizer.extract_fbank_features(wav_path, batch_subsegments)
        )
        offsets = [int(offset) for offset in subsegment_offsets]
        embeddings_batches.append(
            diarizer.generate_embeddings(
                features_flat,
                frames_per_subsegment,
                offsets,
                feature_dim,
            )
        )

    embeddings = np.concatenate(embeddings_batches, axis=0)
    raw_segments, merged_segments, centroids = diarizer.perform_clustering(
        embeddings,
        subsegments,
    )

    diarizer.timing_stats["total_time"] = round(clock() - total_start, 2)
    result: ProviderDiarizationPayload = {
        "raw_segments": raw_segments,
        "raw_speakers_detected": len({segment["speaker"] for segment in raw_segments}),
        "merged_speakers_detected": len({segment["speaker"] for segment in merged_segments}),
        "merged_segments": merged_segments,
        "speaker_centroids": centroids,
        "timing_stats": diarizer.timing_stats,
        "vad": vad_segments,
    }

    if generate_colors:
        from senko.colors import generate_speaker_colors

        result["speaker_color_sets"] = {
            str(index): generate_speaker_colors(merged_segments, index) for index in range(10)
        }

    return cast(ProviderDiarizationPayload, result)
