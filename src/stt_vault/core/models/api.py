from typing import Literal

from pydantic import BaseModel, ConfigDict

from .mod_contracts import EmbeddingSpaceV1

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]


class ApiRecord(BaseModel):
    """Validated boundary between decoded SQLite rows and HTTP responses."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class ConfigResponse(ApiRecord):
    auth_required: bool
    transcribe_model: str
    senko_device: str
    batched_embeddings_requested: bool


class HealthResponse(ApiRecord):
    status: Literal["ok"]


class AuthTokenResponse(ApiRecord):
    access_token: str
    token_type: Literal["bearer"]
    expires_in: int | None


class AssetSummaryResponse(ApiRecord):
    status: Literal["success"]
    summary: str
    title: str
    speaker_names: dict[str, str]


class DatabaseRecord(ApiRecord):
    """Validated representation of decoded SQLite data."""


class FolderResponse(DatabaseRecord):
    id: str
    name: str
    parent_id: str | None
    created_at: int
    updated_at: int


class FolderAssetSummary(DatabaseRecord):
    id: str
    filename: str
    media_type: str
    status: str
    created_at: int
    updated_at: int
    title: str | None = None
    recorded_at: int | None = None
    duration: float | None = None
    error: "ErrorResponse | None" = None
    summary_status: str | None = None
    parent_folder_id: str | None = None


class FolderTreeNodeResponse(FolderResponse):
    children: list["FolderTreeNodeResponse"]
    assets: list[FolderAssetSummary]


class FolderTreeResponse(DatabaseRecord):
    folders: list[FolderTreeNodeResponse]
    assets: list[FolderAssetSummary]


class FolderDeleteResponse(ApiRecord):
    status: Literal["deleted"]


class SpeakerResponse(DatabaseRecord):
    id: str
    display_name: str
    centroid: list[float]
    sample_count: int
    created_at: int
    updated_at: int


class SpeakerDeleteResponse(ApiRecord):
    status: Literal["deleted"]


class SpeakerRecomputeResponse(ApiRecord):
    assets: int


class AssetRetryResponse(ApiRecord):
    status: Literal["queued"]


class AssetUploadResponse(ApiRecord):
    id: str
    status: Literal["queued"]


class AssetBatchUploadItem(ApiRecord):
    path: str
    status: Literal["queued", "failed"]
    id: str | None = None
    detail: str | None = None


class AssetBatchUploadResponse(ApiRecord):
    results: list[AssetBatchUploadItem]


class UploadCompletionResponse(ApiRecord):
    id: str
    status: Literal["queued"]


class UploadProgressResponse(ApiRecord):
    id: str
    filename: str
    size: int
    offset: int


class VisualEventDetectionResponse(ApiRecord):
    events: int


class AssetMoveResponse(ApiRecord):
    id: str
    parent_folder_id: str | None
    updated_at: int


class AssetDeleteResponse(ApiRecord):
    status: Literal["deleted"]


class DiarizationSegment(DatabaseRecord):
    start: float
    end: float
    speaker: str


class DiarizationResult(DatabaseRecord):
    raw_segments: list[DiarizationSegment]
    merged_segments: list[DiarizationSegment]
    speaker_centroids: dict[str, list[float]]
    timing_stats: dict[str, JsonValue]
    embedding_space: EmbeddingSpaceV1 | None = None


class ErrorResponse(ApiRecord):
    category: str | None = None
    message: str


class TimedTranscriptUnitResponse(DatabaseRecord):
    unit_index: int
    text: str
    start_ms: int
    end_ms: int
    confidence: float | None = None
    language: str | None = None
    token_kind: Literal["word", "token", "punctuation", "other"]


class TranscriptResponse(DatabaseRecord):
    chunk_index: int
    start: float
    end: float
    chunk_start: float
    chunk_end: float
    speaker: str
    text: str
    attempts: int | None = None
    speaker_id: str | None = None
    speaker_name: str | None = None
    speaker_similarity: float | None = None
    status: Literal["success", "failed"] | None = None
    error: ErrorResponse | None = None
    updated_at: int | None = None
    timed_units: list[TimedTranscriptUnitResponse] | None = None


class TranscriptChunkRecord(TranscriptResponse):
    asset_id: str


class VisualEventResponse(DatabaseRecord):
    event_index: int
    timestamp: float
    score: float
    kind: str
    created_at: int


class UploadSessionResponse(DatabaseRecord):
    id: str
    filename: str
    total_size: int
    offset: int
    temp_path: str
    created_at: int
    updated_at: int


class AssetResponse(DatabaseRecord):
    id: str
    filename: str
    media_type: str
    status: str
    created_at: int
    updated_at: int
    title: str | None = None
    recorded_at: int | None = None
    parent_folder_id: str | None = None
    original_path: str | None = None
    wav_path: str | None = None
    duration: float | None = None
    error: ErrorResponse | None = None
    diarization_stats: dict[str, JsonValue] | None = None
    raw_segments: list[DiarizationSegment] | None = None
    merged_segments: list[DiarizationSegment] | None = None
    speaker_centroids: dict[str, list[float]] | None = None
    transcript_segments: list[TranscriptResponse] | None = None
    exports: dict[str, str] | None = None
    summary_status: str | None = None
    summary_text: str | None = None
    summary_error: str | None = None
    summary_model: str | None = None
    summary_updated_at: int | None = None
    job: "JobResponse | None" = None
    events: list["EventResponse"] | None = None
    event_history: list["EventResponse"] | None = None
    visual_events: list[VisualEventResponse] | None = None


class JobResponse(DatabaseRecord):
    id: str
    asset_id: str
    status: str
    created_at: int
    stage: str | None = None
    error: ErrorResponse | None = None
    started_at: int | None = None
    finished_at: int | None = None
    progress_total_chunks: int = 0
    progress_done_chunks: int = 0
    progress_failed_chunks: int = 0
    next_retry_at: int | None = None
    run_attempt: int = 0
    claim_owner: str | None = None
    claim_expires_at: int | None = None
    filename: str | None = None
    media_type: str | None = None
    duration: float | None = None


class EventResponse(DatabaseRecord):
    id: int
    level: str
    message: str
    created_at: int
    stage: str | None = None
    payload: dict[str, JsonValue] | None = None
    run_attempt: int = 0
