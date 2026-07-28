from collections.abc import Callable
from functools import wraps
from typing import cast

from stt_vault.processing.diarization_contracts import (
    ClusteringDiarizationProvider,
    DiarizationProvider,
    EmbeddingDiarizationProvider,
    FbankDiarizationProvider,
    InstrumentedDiarizationProvider,
    SubsegmentDiarizationProvider,
    VadDiarizationProvider,
)

StageRecorder = Callable[[str, float, float | None], None]


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


def instrument_diarizer(diarizer: DiarizationProvider, record_stage: StageRecorder) -> None:
    instrumented_diarizer = cast(InstrumentedDiarizationProvider, cast(object, diarizer))
    if getattr(instrumented_diarizer, "_stt_vault_instrumented", False):
        return

    if isinstance(diarizer, VadDiarizationProvider):
        diarizer._perform_vad = _wrap_stage("vad", diarizer._perform_vad, record_stage)
    if isinstance(diarizer, SubsegmentDiarizationProvider):
        diarizer._generate_subsegments = _wrap_stage(
            "subsegments", diarizer._generate_subsegments, record_stage
        )
    if isinstance(diarizer, FbankDiarizationProvider):
        diarizer._extract_fbank_features = _wrap_stage(
            "fbank", diarizer._extract_fbank_features, record_stage
        )
    if isinstance(diarizer, EmbeddingDiarizationProvider):
        diarizer._generate_embeddings = _wrap_stage(
            "embeddings", diarizer._generate_embeddings, record_stage
        )
    if isinstance(diarizer, ClusteringDiarizationProvider):
        diarizer._perform_clustering = _wrap_stage(
            "clustering", diarizer._perform_clustering, record_stage
        )
    instrumented_diarizer._stt_vault_instrumented = True


def _wrap_stage[**P, R](
    stage_name: str,
    method: Callable[P, R],
    record_stage: StageRecorder,
) -> Callable[P, R]:
    @wraps(method)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        import time

        rss_before = current_rss_mb()
        start = time.perf_counter()
        try:
            return method(*args, **kwargs)
        finally:
            record_stage(stage_name, time.perf_counter() - start, rss_before)

    return wrapped
