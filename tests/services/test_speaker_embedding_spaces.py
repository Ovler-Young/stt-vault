import pytest

from stt_vault.core.models.mod_contracts import EmbeddingSpaceV1
from stt_vault.core.models.records import SpeakerMatch, SpeakerRecord
from stt_vault.processing.diarization import match_speakers


def embedding_space() -> EmbeddingSpaceV1:
    return EmbeddingSpaceV1(
        space_id="senko-ecapa",
        model_id="ecapa-tdnn",
        revision="r1",
        sha256="a" * 64,
        dimension=2,
        metric="cosine",
    )


def known_speaker(space: EmbeddingSpaceV1 | None) -> SpeakerRecord:
    return SpeakerRecord("speaker-1", "Alice", (0.2, 0.8), 2, 1, 1, space)


@pytest.mark.parametrize(
    "asset_space,stored_space",
    [
        (None, embedding_space()),
        (embedding_space(), None),
    ],
)
def test_match_speakers_skips_legacy_rows_without_cosine(
    monkeypatch: pytest.MonkeyPatch,
    asset_space: EmbeddingSpaceV1 | None,
    stored_space: EmbeddingSpaceV1 | None,
) -> None:
    calls: list[tuple[list[float], list[float]]] = []
    monkeypatch.setattr(
        "stt_vault.processing.diarization.cosine_similarity",
        lambda left, right: calls.append((left, right)) or 1.0,
    )

    matches = match_speakers(
        {"LOCAL_00": [0.1, 0.9]},
        [known_speaker(stored_space)],
        0.5,
        embedding_space=asset_space,
    )

    assert calls == []
    assert matches == {"LOCAL_00": SpeakerMatch("LOCAL_00", "LOCAL_00", None)}


def test_match_speakers_skips_different_spaces_without_cosine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[float], list[float]]] = []
    monkeypatch.setattr(
        "stt_vault.processing.diarization.cosine_similarity",
        lambda left, right: calls.append((left, right)) or 1.0,
    )
    different_space = EmbeddingSpaceV1(
        space_id="senko-ecapa",
        model_id="ecapa-tdnn",
        revision="r1",
        sha256="b" * 64,
        dimension=2,
        metric="cosine",
    )

    matches = match_speakers(
        {"LOCAL_00": [0.1, 0.9]},
        [known_speaker(different_space)],
        0.5,
        embedding_space=embedding_space(),
    )

    assert calls == []
    assert matches["LOCAL_00"].speaker_id == "LOCAL_00"


def test_match_speakers_compares_exact_cosine_spaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[float], list[float]]] = []
    monkeypatch.setattr(
        "stt_vault.processing.diarization.cosine_similarity",
        lambda left, right: calls.append((left, right)) or 0.9,
    )

    matches = match_speakers(
        {"LOCAL_00": [0.1, 0.9]},
        [known_speaker(embedding_space())],
        0.5,
        embedding_space=embedding_space(),
    )

    assert calls == [([0.1, 0.9], [0.2, 0.8])]
    assert matches["LOCAL_00"].speaker_id == "speaker-1"


@pytest.mark.parametrize(
    "centroid",
    [
        [0.1],
        [0.1, float("nan")],
        [0.0, 0.0],
    ],
)
def test_match_speakers_skips_centroids_incompatible_with_embedding_space(
    monkeypatch: pytest.MonkeyPatch,
    centroid: list[float],
) -> None:
    calls: list[tuple[list[float], list[float]]] = []
    monkeypatch.setattr(
        "stt_vault.processing.diarization.cosine_similarity",
        lambda left, right: calls.append((left, right)) or 1.0,
    )

    matches = match_speakers(
        {"LOCAL_00": centroid},
        [known_speaker(embedding_space())],
        0.5,
        embedding_space=embedding_space(),
    )

    assert calls == []
    assert matches["LOCAL_00"].speaker_id == "LOCAL_00"
