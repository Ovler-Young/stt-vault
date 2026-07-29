import sys
from collections.abc import Callable
from functools import wraps

from .contracts import (
    ClusteringDiarizationProvider,
    DiarizationProvider,
    EmbeddingDiarizationProvider,
    FbankDiarizationProvider,
    SubsegmentDiarizationProvider,
    VadDiarizationProvider,
)

StageRecorder = Callable[[str, float, float | None], None]
_INSTRUMENTED_ATTRIBUTE = "_stt_vault_instrumented"


def current_rss_mb() -> float | None:
    try:
        import resource

        value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except Exception:
        return None

    return _rss_value_to_mb(value, sys.platform)


def _rss_value_to_mb(value: float, platform: str) -> float:
    # Linux reports KiB; macOS reports bytes.
    divisor = 1024 * 1024 if platform == "darwin" else 1024
    return round(value / divisor, 1)


def instrument_diarizer(diarizer: DiarizationProvider, record_stage: StageRecorder) -> None:
    if getattr(diarizer, _INSTRUMENTED_ATTRIBUTE, False):
        return
    try:
        setattr(diarizer, _INSTRUMENTED_ATTRIBUTE, True)
    except (AttributeError, TypeError) as error:
        raise TypeError("Diarization provider does not support instrumentation") from error

    if isinstance(diarizer, VadDiarizationProvider):
        diarizer.perform_vad = _wrap_stage("vad", diarizer.perform_vad, record_stage)
    if isinstance(diarizer, SubsegmentDiarizationProvider):
        diarizer.generate_subsegments = _wrap_stage(
            "subsegments", diarizer.generate_subsegments, record_stage
        )
    if isinstance(diarizer, FbankDiarizationProvider):
        diarizer.extract_fbank_features = _wrap_stage(
            "fbank", diarizer.extract_fbank_features, record_stage
        )
    if isinstance(diarizer, EmbeddingDiarizationProvider):
        diarizer.generate_embeddings = _wrap_stage(
            "embeddings", diarizer.generate_embeddings, record_stage
        )
    if isinstance(diarizer, ClusteringDiarizationProvider):
        diarizer.perform_clustering = _wrap_stage(
            "clustering", diarizer.perform_clustering, record_stage
        )


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
