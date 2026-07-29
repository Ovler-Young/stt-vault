import numpy as np

from stt_vault.processing.diarization.pipeline import run_batched_diarization


class FakeBatchedProvider:
    timing_stats: dict[str, object]

    def __init__(self) -> None:
        self.timing_stats = {}

    def validate_wav_file(self, _wav_file: object, _wav_path: str) -> None:
        return None

    def perform_vad(self, _wav_path: str) -> list[tuple[float, float]]:
        return [(0.0, 1.0)]

    def generate_subsegments(
        self, _vad_segments: list[tuple[float, float]], _accurate: bool | None
    ) -> list[tuple[float, float]]:
        return [(0.0, 1.0)]

    def extract_fbank_features(
        self, _wav_path: str, _subsegments: list[tuple[float, float]]
    ) -> tuple[np.ndarray, list[int], list[int], int]:
        return np.ones((1, 1), dtype=np.float32), [1], [0], 1

    def generate_embeddings(
        self,
        _features: np.ndarray,
        _frames_per_subsegment: list[int],
        _subsegment_offsets: list[int],
        _feature_dim: int,
    ) -> np.ndarray:
        return np.ones((1, 1), dtype=np.float32)

    def perform_clustering(
        self,
        _embeddings: np.ndarray,
        _subsegments: list[tuple[float, float]],
    ) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, np.ndarray]]:
        segment = {"speaker": "speaker-0", "start": 0.0, "end": 1.0}
        return [segment], [segment], {"speaker-0": np.ones(1, dtype=np.float32)}


def test_batched_diarization_uses_injected_clock(tmp_path) -> None:
    wav_path = tmp_path / "silence.wav"
    wav_path.write_bytes(
        b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
        b"\x44\xac\x00\x00\x88\x58\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
    )
    times = iter((10.0, 12.5))

    result = run_batched_diarization(
        FakeBatchedProvider(),
        str(wav_path),
        fbank_batch_segments=1,
        clock=lambda: next(times),
    )

    assert result is not None
    assert result["timing_stats"]["total_time"] == 2.5
