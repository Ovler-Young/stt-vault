import wave
from pathlib import Path

import numpy as np
import pytest

from stt_vault.processing.diarization.senko import SenkoDiarizationProvider


def test_senko_adapter_rejects_incomplete_implementation() -> None:
    class IncompleteImplementation:
        def diarize(self, _wav_path: str, *, generate_colors: bool) -> None:
            return None

    with pytest.raises(TypeError, match="required stage operations"):
        SenkoDiarizationProvider(IncompleteImplementation())


def test_senko_adapter_exposes_public_stage_contract(tmp_path: Path) -> None:
    calls: list[str] = []

    class Implementation:
        _timing_stats = {"initial": 1}

        def diarize(self, _wav_path: str, *, generate_colors: bool) -> None:
            calls.append(f"diarize:{generate_colors}")

        def _validate_wav_file(self, _wav_file: wave.Wave_read, _wav_path: str) -> None:
            calls.append("validate")

        def _perform_vad(self, _wav_path: str) -> list[tuple[float, float]]:
            calls.append("vad")
            return [(0.0, 1.0)]

        def _generate_subsegments(
            self, _vad_segments: list[tuple[float, float]], _accurate: bool | None
        ) -> list[tuple[float, float]]:
            calls.append("subsegments")
            return [(0.0, 0.5)]

        def _extract_fbank_features(
            self, _wav_path: str, _subsegments: list[tuple[float, float]]
        ) -> tuple[np.ndarray, list[int], list[int], int]:
            calls.append("fbank")
            return np.zeros((1, 1), dtype=np.float32), [1], [0], 1

        def _generate_embeddings(
            self,
            _features: np.ndarray,
            _frames: list[int],
            _offsets: list[int],
            _feature_dim: int,
        ) -> np.ndarray:
            calls.append("embeddings")
            return np.zeros((1, 1), dtype=np.float32)

        def _perform_clustering(
            self,
            _embeddings: np.ndarray,
            _subsegments: list[tuple[float, float]],
        ) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, np.ndarray]]:
            calls.append("clustering")
            return [], [], {}

    implementation = Implementation()
    provider = SenkoDiarizationProvider(implementation)

    assert provider.timing_stats == {"initial": 1}
    provider.timing_stats = {"updated": 2}
    assert implementation._timing_stats == {"updated": 2}
    wav_path = tmp_path / "audio.wav"
    with wave.open(str(wav_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(b"\x00\x00")
    with wave.open(str(wav_path), "rb") as wav_file:
        provider.validate_wav_file(wav_file, str(wav_path))
    provider.diarize("audio.wav", generate_colors=True)
    provider.perform_vad("audio.wav")
    provider.generate_subsegments([(0.0, 1.0)], None)
    provider.extract_fbank_features("audio.wav", [(0.0, 0.5)])
    provider.generate_embeddings(np.zeros((1, 1)), [1], [0], 1)
    provider.perform_clustering(np.zeros((1, 1)), [(0.0, 0.5)])

    assert calls == [
        "validate",
        "diarize:True",
        "vad",
        "subsegments",
        "fbank",
        "embeddings",
        "clustering",
    ]
