from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import ValidationError

from stt_vault.core.config import Settings
from stt_vault.core.models.mod_contracts import (
    DiarizationResponseV1,
    EmbeddingSpaceV1,
    ModErrorV1,
    TimedUnitsCapabilityV1,
    TranscriptionRequestV1,
    TranscriptionResponseV1,
)


def _mod() -> dict[str, object]:
    return {
        "id": "mod-whisper-cpu",
        "version": "1.0.0",
        "image_digest": "sha256:" + "a" * 64,
        "runtime": "whisper.cpp",
        "model": {
            "id": "ggml-base.en.bin",
            "revision": "v1",
            "sha256": "a" * 64,
            "license_ref": "MIT",
            "access_declaration": "public",
        },
    }


def _embedding(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "space_id": "pyannote-speaker-v1",
        "model_id": "pyannote.speaker-diarization",
        "revision": "2026.08",
        "sha256": "b" * 64,
        "dimension": 3,
        "metric": "cosine",
    }
    value.update(overrides)
    return value


def test_transcription_request_rejects_invalid_contract_and_uuid() -> None:
    request = {
        "contract_version": "v2",
        "correlation_id": str(uuid4()),
        "idempotency_key": str(uuid4()),
        "asset_id": "asset:1",
        "chunk": {"index": 0, "start": 0.0, "end": 1.0, "speaker_id": "speaker:1"},
        "language": None,
        "prompt": None,
    }
    with pytest.raises(ValidationError):
        TranscriptionRequestV1.model_validate(request)

    request["contract_version"] = "v1"
    request["correlation_id"] = "not-a-uuid"
    with pytest.raises(ValidationError):
        TranscriptionRequestV1.model_validate(request)

    request["correlation_id"] = "00000000-0000-1000-8000-000000000000"
    with pytest.raises(ValidationError):
        TranscriptionRequestV1.model_validate(request)


def test_transcription_response_rejects_nonfinite_overlapping_and_empty_segments() -> None:
    response = {
        "contract_version": "v1",
        "correlation_id": str(uuid4()),
        "mod": _mod(),
        "result": {
            "kind": "speech",
            "segments": [
                {"start": 0.0, "end": 1.0, "text": "first"},
                {"start": 0.5, "end": float("inf"), "text": " "},
            ],
        },
    }
    with pytest.raises(ValidationError):
        TranscriptionResponseV1.model_validate(response, context={"chunk_duration": 2.0})


def test_timed_units_require_a_valid_capability_and_valid_chunk_relative_values() -> None:
    capability = TimedUnitsCapabilityV1.model_validate(
        {
            "unit_kinds": ["word", "punctuation"],
            "time_base": "chunk_ms",
            "precision_ms": 20,
        }
    )
    response = {
        "contract_version": "v1",
        "correlation_id": str(uuid4()),
        "mod": _mod(),
        "result": {
            "kind": "speech",
            "segments": [{"start": 0.0, "end": 1.0, "text": "你好，"}],
            "timed_units": [
                {
                    "unit_index": 0,
                    "text": "你好",
                    "start_ms": 0,
                    "end_ms": 500,
                    "confidence": 1,
                    "language": "zh-Hans",
                    "token_kind": "word",
                },
                {
                    "unit_index": 1,
                    "text": "，",
                    "start_ms": 500,
                    "end_ms": 500,
                    "confidence": None,
                    "language": "zh-Hans",
                    "token_kind": "punctuation",
                },
            ],
        },
    }
    context = {"chunk_duration": 1.0, "timed_units_capability": capability}
    parsed = TranscriptionResponseV1.model_validate(response, context=context)
    assert [unit.text for unit in parsed.result.timed_units or []] == ["你好", "，"]

    for unit_override in (
        {"start_ms": 1},
        {"end_ms": 1001},
        {"confidence": 1.1},
        {"language": "not a language"},
        {"token_kind": "token"},
    ):
        invalid = deepcopy(response)
        invalid["result"]["timed_units"][0].update(unit_override)
        with pytest.raises(ValidationError):
            TranscriptionResponseV1.model_validate(invalid, context=context)

    with pytest.raises(ValidationError):
        TimedUnitsCapabilityV1.model_validate(
            {"unit_kinds": ["word", "word"], "time_base": "chunk_ms", "precision_ms": 20}
        )
    with pytest.raises(ValidationError):
        TimedUnitsCapabilityV1.model_validate(
            {"unit_kinds": ["word"], "time_base": "chunk_ms", "precision_ms": "20"}
        )


def test_diarization_response_requires_cosine_finite_compatible_embeddings() -> None:
    response = {
        "contract_version": "v1",
        "correlation_id": str(uuid4()),
        "mod": _mod(),
        "embedding": _embedding(),
        "result": {
            "raw_segments": [{"start": 0.0, "end": 1.0, "speaker_id": "speaker:1"}],
            "merged_segments": [{"start": 0.0, "end": 1.0, "speaker_id": "speaker:1"}],
            "speaker_centroids": {"speaker:1": [0.5, 0.5, 0.5]},
            "timing_stats": {"inference": 0.1},
        },
    }
    DiarizationResponseV1.model_validate(response, context={"audio_duration": 1.0})

    response["embedding"] = _embedding(metric="euclidean")
    with pytest.raises(ValidationError):
        DiarizationResponseV1.model_validate(response, context={"audio_duration": 1.0})

    response["embedding"] = _embedding()
    response["result"]["speaker_centroids"] = {"speaker:1": [0.5, float("nan"), 0.5]}
    with pytest.raises(ValidationError):
        DiarizationResponseV1.model_validate(response, context={"audio_duration": 1.0})

    response["result"]["speaker_centroids"] = {"speaker:1": [0.5, 0.5]}
    with pytest.raises(ValidationError, match="dimension"):
        DiarizationResponseV1.model_validate(response, context={"audio_duration": 1.0})

    response["result"]["speaker_centroids"] = {"speaker:1": [0.0, 0.0, 0.0]}
    with pytest.raises(ValidationError, match="norm must be nonzero"):
        DiarizationResponseV1.model_validate(response, context={"audio_duration": 1.0})


def test_embedding_space_is_a_cosine_tuple_and_errors_are_categorized() -> None:
    assert EmbeddingSpaceV1.model_validate(_embedding()).metric == "cosine"
    error = ModErrorV1.model_validate(
        {
            "contract_version": "v1",
            "correlation_id": str(uuid4()),
            "mod": _mod(),
            "error": {
                "category": "provider_failure",
                "message": "provider unavailable",
                "retryable": True,
            },
        }
    )
    assert error.error.retryable is True


def test_settings_select_one_provider_per_role_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        stt_transcription_provider="mod-whisper-cpu",
        stt_diarization_provider="senko",
        mod_whisper_cpu_image_digest="sha256:" + "a" * 64,
    )
    assert settings.stt_transcription_provider == "mod-whisper-cpu"
    assert settings.stt_diarization_provider == "senko"

    monkeypatch.delenv("STT_TRANSCRIPTION_PROVIDER", raising=False)
    monkeypatch.delenv("STT_DIARIZATION_PROVIDER", raising=False)
    with pytest.raises(ValidationError):
        Settings()

    with pytest.raises(ValidationError):
        Settings(stt_transcription_provider="senko", stt_diarization_provider="senko")


def test_settings_builds_the_configured_senko_embedding_space() -> None:
    settings = Settings(
        stt_transcription_provider="openai",
        stt_diarization_provider="senko",
        senko_embedding_space_id="senko-campplus-192-cosine",
        senko_embedding_model_id="speech-campplus-sv-zh-en-16k-common-advanced",
        senko_embedding_revision="ba0e12ed923ff49e8c2d9d9a3e42d7923cb95724",
        senko_embedding_sha256="a" * 64,
        senko_embedding_dimension=192,
    )

    assert settings.senko_embedding_space == EmbeddingSpaceV1(
        space_id="senko-campplus-192-cosine",
        model_id="speech-campplus-sv-zh-en-16k-common-advanced",
        revision="ba0e12ed923ff49e8c2d9d9a3e42d7923cb95724",
        sha256="a" * 64,
        dimension=192,
        metric="cosine",
    )


@pytest.mark.parametrize("digest", ["", "sha256:not-a-digest", "sha512:" + "a" * 64])
def test_selected_mod_requires_a_verified_image_digest(digest: str) -> None:
    with pytest.raises(ValidationError, match="STT_MOD_WHISPER_CPU_DIGEST"):
        Settings(
            stt_transcription_provider="mod-whisper-cpu",
            stt_diarization_provider="senko",
            mod_whisper_cpu_image_digest=digest,
        )
