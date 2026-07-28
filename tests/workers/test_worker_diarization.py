import json
import logging
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from stt_vault.core.diagnostics.logging import StructuredFormatter
from stt_vault.core.models.api import JsonValue
from stt_vault.core.models.records import SpeakerSegment
from stt_vault.processing.diarization import DiarizerManager, ProviderDiarizationPayload


def test_batched_diarization_logs_json_without_provider_console_output(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    wav_path = tmp_path / "private.wav"
    with wave.open(str(wav_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(b"\x00\x00")

    provider = SimpleNamespace(
        diarize=lambda _wav_path, *, generate_colors: None,
        _validate_wav_file=lambda _wav_file, _path: None,
        _perform_vad=lambda _path: [],
    )
    manager = DiarizerManager(device="cpu", idle_timeout_seconds=1)

    with caplog.at_level(logging.INFO, logger="stt_vault.processing.diarization"):
        assert manager._diarize_batched(provider, str(wav_path)) is None

    record = caplog.records[-1]
    event = json.loads(StructuredFormatter().format(record))
    assert event["event_name"] == "diarization.started"
    assert event["media_filename"] == "private.wav"
    assert str(wav_path.parent) not in StructuredFormatter().format(record)


def test_batched_diarization_instruments_each_provider_operation(tmp_path: Path) -> None:
    wav_path = tmp_path / "audio.wav"
    with wave.open(str(wav_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(b"\x00\x00")

    class BatchedProvider:
        _timing_stats: dict[str, JsonValue] = {}

        def diarize(self, _wav_path: str, *, generate_colors: bool) -> None:
            raise AssertionError("batched diarization must use the provider stages")

        def _validate_wav_file(self, _wav_file: wave.Wave_read, _wav_path: str) -> None:
            return None

        def _perform_vad(self, _wav_path: str) -> list[tuple[float, float]]:
            return [(0.0, 1.0)]

        def _generate_subsegments(
            self, vad_segments: list[tuple[float, float]], _accurate: bool | None
        ) -> list[tuple[float, float]]:
            return vad_segments

        def _extract_fbank_features(
            self, _wav_path: str, _subsegments: list[tuple[float, float]]
        ) -> tuple[np.ndarray, list[int], list[int], int]:
            return np.array([[1.0]], dtype=np.float32), [1], [0], 1

        def _generate_embeddings(
            self,
            _features: np.ndarray,
            _frames: list[int],
            _offsets: list[int],
            _feature_dim: int,
        ) -> np.ndarray:
            return np.array([[0.5]], dtype=np.float32)

        def _perform_clustering(
            self, _embeddings: np.ndarray, _subsegments: list[tuple[float, float]]
        ) -> tuple[list[SpeakerSegment], list[SpeakerSegment], dict[str, np.ndarray]]:
            segments = [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]
            return segments, segments, {"SPEAKER_00": np.array([0.5], dtype=np.float32)}

    provider = BatchedProvider()
    manager = DiarizerManager(
        device="cpu",
        idle_timeout_seconds=1,
        use_batched_embeddings=True,
        diarizer_factory=lambda _device: provider,
    )

    result = manager.diarize(str(wav_path))

    assert result is not None
    assert len(result.raw_segments) == 1
    assert len(result.merged_segments) == 1
    assert set(manager._resource_stats) == {
        "load_diarizer",
        "vad",
        "subsegments",
        "fbank",
        "embeddings",
        "clustering",
    }


def test_batched_factory_payload_accepts_senko_interval_shape(tmp_path: Path) -> None:
    wav_path = tmp_path / "audio.wav"
    with wave.open(str(wav_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(b"\x00\x00")

    class TupleProvider:
        _timing_stats: dict[str, JsonValue] = {}

        def diarize(self, _wav_path: str, *, generate_colors: bool) -> ProviderDiarizationPayload:
            raise AssertionError("the batched path must use stage operations")

        def _validate_wav_file(self, _wav_file: wave.Wave_read, _wav_path: str) -> None:
            return None

        def _perform_vad(self, _wav_path: str) -> list[tuple[float, float]]:
            return [(0.0, 1.0)]

        def _generate_subsegments(
            self, vad_segments: list[tuple[float, float]], _accurate: bool | None
        ) -> list[tuple[float, float]]:
            return vad_segments

        def _extract_fbank_features(
            self, _wav_path: str, _subsegments: list[tuple[float, float]]
        ) -> tuple[np.ndarray, list[int], list[int], int]:
            return np.array([[1.0]], dtype=np.float32), [1], [0], 1

        def _generate_embeddings(
            self,
            _features: np.ndarray,
            _frames: list[int],
            _offsets: list[int],
            _feature_dim: int,
        ) -> np.ndarray:
            return np.array([[0.5]], dtype=np.float32)

        def _perform_clustering(
            self, _embeddings: np.ndarray, _subsegments: list[tuple[float, float]]
        ) -> tuple[list[SpeakerSegment], list[SpeakerSegment], dict[str, np.ndarray]]:
            segments = [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]
            return segments, segments, {"SPEAKER_00": np.array([0.5], dtype=np.float32)}

    manager = DiarizerManager(
        device="cpu",
        idle_timeout_seconds=1,
        use_batched_embeddings=True,
        diarizer_factory=lambda _device: TupleProvider(),
    )

    result = manager.diarize(str(wav_path))

    assert result is not None
    assert result.raw_segments[0].speaker == "SPEAKER_00"
