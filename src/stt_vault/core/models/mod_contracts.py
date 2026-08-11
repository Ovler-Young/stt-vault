"""Typed internal v1 contracts for local transcription and diarization Mods."""

import math
import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

CONTRACT_VERSION_V1 = "v1"

_ID_PATTERN = r"^[A-Za-z0-9._:-]+$"
_SHA256_PATTERN = r"^[a-f0-9]{64}$"
_IMAGE_DIGEST_PATTERN = r"^sha256:[a-f0-9]{64}$"
_SEMVER_PATTERN = (
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_CSS_HEX_PATTERN = r"^#[0-9A-Fa-f]{3}(?:[0-9A-Fa-f]{1}|[0-9A-Fa-f]{3}|[0-9A-Fa-f]{5})?$"

ContractVersionV1 = Literal["v1"]
ModErrorCategory = Literal[
    "unavailable",
    "not_ready",
    "unsupported",
    "invalid_request",
    "resource_exhausted",
    "provider_failure",
    "contract_incompatible",
]


class ModContractModel(BaseModel):
    """Base model that preserves forward-compatible optional wire fields."""

    model_config = ConfigDict(extra="ignore")


def _validate_finite(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("must be finite")
    return value


def _validate_uuid_v4(value: UUID) -> UUID:
    if value.version != 4:
        raise ValueError("must be a UUIDv4")
    return value


class ModelIdentityV1(ModContractModel):
    id: Annotated[str, Field(pattern=_ID_PATTERN, min_length=1)]
    revision: Annotated[str, Field(min_length=1)]
    sha256: Annotated[str, Field(pattern=_SHA256_PATTERN)]
    license_ref: Annotated[str, Field(min_length=1)]
    access_declaration: Annotated[str, Field(min_length=1)]


class ModIdentityV1(ModContractModel):
    id: Annotated[str, Field(pattern=_ID_PATTERN, min_length=1)]
    version: Annotated[str, Field(pattern=_SEMVER_PATTERN)]
    image_digest: Annotated[str, Field(pattern=_IMAGE_DIGEST_PATTERN)]
    runtime: Annotated[str, Field(min_length=1)]
    model: ModelIdentityV1


class EmbeddingSpaceV1(ModContractModel):
    space_id: Annotated[str, Field(pattern=_ID_PATTERN, min_length=1)]
    model_id: Annotated[str, Field(pattern=_ID_PATTERN, min_length=1)]
    revision: Annotated[str, Field(min_length=1)]
    sha256: Annotated[str, Field(pattern=_SHA256_PATTERN)]
    dimension: Annotated[int, Field(ge=1)]
    metric: Literal["cosine"]


class ModSuccessV1(ModContractModel):
    contract_version: ContractVersionV1
    correlation_id: UUID
    mod: ModIdentityV1

    @field_validator("correlation_id")
    @classmethod
    def correlation_id_is_uuid_v4(cls, value: UUID) -> UUID:
        return _validate_uuid_v4(value)


class ModErrorDetailV1(ModContractModel):
    category: ModErrorCategory
    message: Annotated[str, Field(min_length=1)]
    retryable: bool

    @model_validator(mode="after")
    def retry_semantics_match_category(self) -> "ModErrorDetailV1":
        retryable_categories = {"unavailable", "not_ready", "resource_exhausted"}
        terminal_categories = {"unsupported", "invalid_request", "contract_incompatible"}
        if self.category in retryable_categories and not self.retryable:
            raise ValueError(f"{self.category} errors must be retryable")
        if self.category in terminal_categories and self.retryable:
            raise ValueError(f"{self.category} errors must not be retryable")
        return self


class ModErrorV1(ModSuccessV1):
    error: ModErrorDetailV1


class TranscriptionChunkV1(ModContractModel):
    index: Annotated[int, Field(ge=0)]
    start: float
    end: float
    speaker_id: Annotated[str, Field(pattern=_ID_PATTERN, min_length=1)]

    @field_validator("start", "end")
    @classmethod
    def bounds_are_finite(cls, value: float) -> float:
        return _validate_finite(value)

    @model_validator(mode="after")
    def end_is_after_start(self) -> "TranscriptionChunkV1":
        if self.start >= self.end:
            raise ValueError("chunk end must be greater than start")
        return self


class TranscriptionRequestV1(ModContractModel):
    contract_version: ContractVersionV1
    correlation_id: UUID
    idempotency_key: UUID
    asset_id: Annotated[str, Field(pattern=_ID_PATTERN, min_length=1)]
    chunk: TranscriptionChunkV1
    language: str | None
    prompt: str | None

    @field_validator("correlation_id", "idempotency_key")
    @classmethod
    def request_ids_are_uuid_v4(cls, value: UUID) -> UUID:
        return _validate_uuid_v4(value)


class TranscriptionSegmentV1(ModContractModel):
    start: float
    end: float
    text: str

    @field_validator("start", "end")
    @classmethod
    def bounds_are_finite(cls, value: float) -> float:
        return _validate_finite(value)

    @model_validator(mode="after")
    def has_valid_interval(self) -> "TranscriptionSegmentV1":
        if self.start < 0 or self.start >= self.end:
            raise ValueError("segment must satisfy 0 <= start < end")
        return self


class TranscriptionResultV1(ModContractModel):
    kind: Literal["speech", "no_speech"]
    segments: list[TranscriptionSegmentV1]

    @model_validator(mode="after")
    def segments_match_kind_and_do_not_overlap(self) -> "TranscriptionResultV1":
        if self.kind == "no_speech" and self.segments:
            raise ValueError("no_speech results must not include segments")
        if self.kind == "speech" and any(not segment.text.strip() for segment in self.segments):
            raise ValueError("speech segment text must be nonempty after trimming")
        for previous, current in zip(self.segments, self.segments[1:], strict=False):
            if current.start < previous.end:
                raise ValueError("segments must be ordered and non-overlapping")
        return self


class TranscriptionResponseV1(ModSuccessV1):
    result: TranscriptionResultV1

    @model_validator(mode="after")
    def segments_stay_within_chunk_duration(
        self, info: ValidationInfo
    ) -> "TranscriptionResponseV1":
        chunk_duration = info.context.get("chunk_duration") if info.context else None
        if chunk_duration is None:
            return self
        duration = _validate_finite(float(chunk_duration))
        if duration < 0:
            raise ValueError("chunk_duration must be nonnegative")
        if any(segment.end > duration + 0.050 for segment in self.result.segments):
            raise ValueError("segment ends after the allowed chunk tolerance")
        return self


class DiarizationRequestV1(ModContractModel):
    contract_version: ContractVersionV1
    correlation_id: UUID
    idempotency_key: UUID
    asset_id: Annotated[str, Field(pattern=_ID_PATTERN, min_length=1)]
    generate_colors: bool

    @field_validator("correlation_id", "idempotency_key")
    @classmethod
    def request_ids_are_uuid_v4(cls, value: UUID) -> UUID:
        return _validate_uuid_v4(value)


class DiarizationSegmentV1(ModContractModel):
    start: float
    end: float
    speaker_id: Annotated[str, Field(pattern=_ID_PATTERN, min_length=1)]

    @field_validator("start", "end")
    @classmethod
    def bounds_are_finite(cls, value: float) -> float:
        return _validate_finite(value)

    @model_validator(mode="after")
    def has_valid_interval(self) -> "DiarizationSegmentV1":
        if self.start < 0 or self.start >= self.end:
            raise ValueError("segment must satisfy 0 <= start < end")
        return self


class DiarizationIntervalV1(ModContractModel):
    start: float
    end: float

    @field_validator("start", "end")
    @classmethod
    def bounds_are_finite(cls, value: float) -> float:
        return _validate_finite(value)

    @model_validator(mode="after")
    def has_valid_interval(self) -> "DiarizationIntervalV1":
        if self.start < 0 or self.start >= self.end:
            raise ValueError("interval must satisfy 0 <= start < end")
        return self


class DiarizationResultV1(ModContractModel):
    raw_segments: list[DiarizationSegmentV1]
    merged_segments: list[DiarizationSegmentV1]
    speaker_centroids: dict[Annotated[str, Field(pattern=_ID_PATTERN, min_length=1)], list[float]]
    timing_stats: dict[Annotated[str, Field(min_length=1)], float]
    vad: list[DiarizationIntervalV1] | None = None
    speaker_color_sets: (
        dict[Annotated[str, Field(pattern=_ID_PATTERN, min_length=1)], str] | None
    ) = None
    raw_speakers_detected: Annotated[int, Field(ge=0)] | None = None
    merged_speakers_detected: Annotated[int, Field(ge=0)] | None = None

    @field_validator("timing_stats")
    @classmethod
    def timings_are_finite(cls, value: dict[str, float]) -> dict[str, float]:
        for timing in value.values():
            _validate_finite(timing)
        return value

    @field_validator("speaker_color_sets")
    @classmethod
    def colors_are_css_hex(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is not None and any(
            not re.fullmatch(_CSS_HEX_PATTERN, color) for color in value.values()
        ):
            raise ValueError("speaker colors must be CSS hexadecimal values")
        return value

    @model_validator(mode="after")
    def intervals_are_ordered_and_non_overlapping(self) -> "DiarizationResultV1":
        for name, segments in (
            ("raw_segments", self.raw_segments),
            ("merged_segments", self.merged_segments),
        ):
            for previous, current in zip(segments, segments[1:], strict=False):
                if current.start < previous.end:
                    raise ValueError(f"{name} must be ordered and non-overlapping")
        if self.vad is not None:
            for previous, current in zip(self.vad, self.vad[1:], strict=False):
                if current.start < previous.end:
                    raise ValueError("vad must be ordered and non-overlapping")
        return self


class DiarizationResponseV1(ModSuccessV1):
    embedding: EmbeddingSpaceV1
    result: DiarizationResultV1

    @model_validator(mode="after")
    def response_matches_embedding_and_audio_duration(
        self, info: ValidationInfo
    ) -> "DiarizationResponseV1":
        for centroid in self.result.speaker_centroids.values():
            if len(centroid) != self.embedding.dimension:
                raise ValueError("centroid dimension does not match embedding metadata")
            if any(not math.isfinite(value) for value in centroid):
                raise ValueError("centroid values must be finite")
            if not any(value != 0 for value in centroid):
                raise ValueError("centroid norm must be nonzero")

        audio_duration = info.context.get("audio_duration") if info.context else None
        if audio_duration is None:
            return self
        duration = _validate_finite(float(audio_duration))
        if duration < 0:
            raise ValueError("audio_duration must be nonnegative")
        intervals = [
            *self.result.raw_segments,
            *self.result.merged_segments,
            *(self.result.vad or []),
        ]
        if any(interval.end > duration for interval in intervals):
            raise ValueError("interval ends after audio duration")
        return self


class ModLiveResponseV1(ModContractModel):
    status: Literal["live"]


class ReadyModelIdentityV1(ModContractModel):
    id: Annotated[str, Field(pattern=_ID_PATTERN, min_length=1)]
    revision: Annotated[str, Field(min_length=1)]
    sha256: Annotated[str, Field(pattern=_SHA256_PATTERN)]


class ModReadyResponseV1(ModContractModel):
    status: Literal["ready"]
    model: ReadyModelIdentityV1
    rss_mb: Annotated[float, Field(ge=0)]

    @field_validator("rss_mb")
    @classmethod
    def rss_is_finite(cls, value: float) -> float:
        return _validate_finite(value)


class ModCapabilityOfferingV1(ModContractModel):
    model_id: Annotated[str, Field(pattern=_ID_PATTERN, min_length=1)]
    device_id: Annotated[str, Field(pattern=_ID_PATTERN, min_length=1)]
    embedding: EmbeddingSpaceV1 | None = None


class ModCapabilitiesResultV1(ModContractModel):
    offerings: list[ModCapabilityOfferingV1]
    max_audio_bytes: Annotated[int, Field(ge=1)]
    max_audio_seconds: Annotated[float, Field(gt=0)]
    readiness: Literal["loading", "ready", "failed"]

    @field_validator("max_audio_seconds")
    @classmethod
    def max_audio_seconds_is_finite(cls, value: float) -> float:
        return _validate_finite(value)


class ModCapabilitiesV1(ModSuccessV1):
    result: ModCapabilitiesResultV1
