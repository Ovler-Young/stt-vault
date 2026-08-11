from pathlib import Path
from types import SimpleNamespace

import pytest

from stt_vault.core.models.mod_contracts import EmbeddingSpaceV1
from stt_vault.workers.worker_media import DiarizationStage

EMBEDDING_SPACE = EmbeddingSpaceV1(
    space_id="test-space",
    model_id="test-model",
    revision="r1",
    sha256="a" * 64,
    dimension=2,
    metric="cosine",
)


def test_diarization_stage_persists_embedding_space_on_the_injected_database(
    tmp_path: Path,
) -> None:
    commands: list[object] = []
    result = SimpleNamespace(
        timing_stats={},
        raw_segments=[],
        merged_segments=[],
        speaker_centroids={"SPEAKER_00": [0.1, 0.2]},
        embedding_space=EMBEDDING_SPACE,
    )
    database = SimpleNamespace(
        update_stage=lambda **_kwargs: None,
        update_diarization_metadata=commands.append,
    )
    stage = DiarizationStage(
        SimpleNamespace(), SimpleNamespace(diarize=lambda _path: result), database
    )

    prepared = stage.diarize("asset-1", tmp_path / "audio.wav", 1.0)

    assert prepared.embedding_space == EMBEDDING_SPACE
    assert commands[0].embedding_space == EMBEDDING_SPACE


@pytest.mark.parametrize("centroid", [[0.1], [0.1, float("nan")], [0.0, 0.0]])
def test_diarization_stage_rejects_invalid_centroids_before_persisting(
    tmp_path: Path, centroid: list[float]
) -> None:
    result = SimpleNamespace(
        timing_stats={},
        raw_segments=[],
        merged_segments=[],
        speaker_centroids={"SPEAKER_00": centroid},
        embedding_space=EMBEDDING_SPACE,
    )
    database = SimpleNamespace(
        update_stage=lambda **_kwargs: None,
        update_diarization_metadata=lambda _command: pytest.fail("must not persist"),
    )
    stage = DiarizationStage(
        SimpleNamespace(), SimpleNamespace(diarize=lambda _path: result), database
    )

    with pytest.raises(ValueError):
        stage.diarize("asset-1", tmp_path / "audio.wav", 1.0)
