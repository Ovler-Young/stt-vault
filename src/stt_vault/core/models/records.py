from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Literal
from uuid import uuid4

from .mod_contracts import EmbeddingSpaceV1


@dataclass(frozen=True)
class ErrorRecord:
    category: str
    message: str
    cause: str | None = None


@dataclass(frozen=True)
class ExportPaths:
    json: str | None = None
    whisper_json: str | None = None
    ai_text: str | None = None
    srt: str | None = None
    vtt: str | None = None
    hyperaudio_html: str | None = None
    rttm: str | None = None
    visual_events: str | None = None


@dataclass(frozen=True)
class SpeakerSegment:
    start: float
    end: float
    speaker: str


@dataclass(frozen=True)
class TranscriptChunk(SpeakerSegment):
    chunk_index: int


@dataclass(frozen=True)
class TranscriptSegment(SpeakerSegment):
    text: str
    chunk_index: int | None = None
    chunk_start: float | None = None
    chunk_end: float | None = None
    attempts: int | None = None
    speaker_id: str | None = None
    speaker_name: str | None = None
    speaker_similarity: float | None = None
    timed_units: tuple["PersistedTimedTranscriptUnit", ...] = ()


@dataclass(frozen=True)
class VisualEvent:
    timestamp: float
    score: float
    kind: str = "slide_change"


@dataclass(frozen=True)
class PersistedVisualEvent(VisualEvent):
    event_index: int = 0
    created_at: int = 0


@dataclass(frozen=True)
class SpeakerMatch:
    speaker_id: str
    display_name: str
    score: float | None


@dataclass(frozen=True)
class SpeakerRecord:
    id: str
    display_name: str
    centroid: tuple[float, ...]
    sample_count: int
    created_at: int
    updated_at: int
    embedding_space: EmbeddingSpaceV1 | None = None


@dataclass(frozen=True)
class ImmutableJsonObject:
    entries: tuple[tuple[str, object], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "entries",
            tuple((str(key), self._freeze(value)) for key, value in self.entries),
        )

    @classmethod
    def from_value(cls, value: object) -> "ImmutableJsonObject":
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            raise ValueError("JSON object values must be mappings")
        return cls(tuple(value.items()))

    @classmethod
    def _freeze(cls, value: object) -> object:
        if isinstance(value, dict):
            return cls.from_value(value)
        if isinstance(value, (list, tuple)):
            return tuple(cls._freeze(item) for item in value)
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        raise ValueError("JSON object values must contain JSON-compatible values")

    @classmethod
    def _thaw(cls, value: object) -> object:
        if isinstance(value, cls):
            return value.as_dict()
        if isinstance(value, tuple):
            return [cls._thaw(item) for item in value]
        return value

    def as_dict(self) -> dict[str, object]:
        return {key: self._thaw(value) for key, value in self.entries}

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ImmutableJsonObject):
            return self.entries == other.entries
        if isinstance(other, dict):
            return self.as_dict() == other
        return NotImplemented


@dataclass(frozen=True)
class SpeakerCentroids:
    entries: tuple[tuple[str, tuple[float, ...]], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "entries",
            tuple(
                (str(name), tuple(float(value) for value in centroid))
                for name, centroid in self.entries
            ),
        )

    @classmethod
    def from_value(cls, value: object) -> "SpeakerCentroids":
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            raise ValueError("speaker centroids must be a mapping")
        return cls(tuple(value.items()))

    def as_dict(self) -> dict[str, tuple[float, ...]]:
        return dict(self.entries)


@dataclass(frozen=True)
class AssetRecord:
    id: str
    filename: str
    media_type: str
    original_path: str
    status: str
    created_at: int
    updated_at: int
    title: str | None = None
    recorded_at: int | None = None
    wav_path: str | None = None
    duration: float | None = None
    error: ErrorRecord | None = None
    diarization_stats: ImmutableJsonObject | None = None
    exports: ExportPaths = ExportPaths()
    raw_segments: tuple[SpeakerSegment, ...] = ()
    merged_segments: tuple[SpeakerSegment, ...] = ()
    transcript_segments: tuple[TranscriptSegment, ...] = ()
    speaker_centroids: SpeakerCentroids = SpeakerCentroids()

    def __post_init__(self) -> None:
        if self.diarization_stats is not None:
            object.__setattr__(
                self, "diarization_stats", ImmutableJsonObject.from_value(self.diarization_stats)
            )
        object.__setattr__(
            self,
            "speaker_centroids",
            SpeakerCentroids.from_value(self.speaker_centroids),
        )

    embedding_space: EmbeddingSpaceV1 | None = None
    summary_status: str | None = None
    summary_text: str | None = None
    summary_error: str | None = None
    summary_model: str | None = None
    summary_updated_at: int | None = None
    job: "JobRecord | None" = None
    events: tuple["JobEventRecord", ...] | None = None
    event_history: tuple["JobEventRecord", ...] | None = None
    visual_events: tuple[PersistedVisualEvent, ...] = ()


@dataclass(frozen=True)
class CleanupTask:
    asset_id: str
    media_path: str
    exports_path: str


@dataclass(frozen=True)
class AudioStream:
    audio_index: int
    stream_index: int | None
    codec_name: str | None
    channels: int | None
    channel_layout: str | None
    bit_rate: str | None
    language: str | None
    title: str | None


@dataclass(frozen=True)
class UploadSessionRecord:
    id: str
    filename: str
    total_size: int
    offset: int
    temp_path: str
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class UploadResponse:
    id: str
    filename: str
    size: int
    offset: int


ProviderInvocationState = Literal[
    "prepared", "sent", "accepted", "completed", "cancelled", "failed"
]
RecoveryPhase = Literal["claimed", "transcoding", "diarizing", "transcribing speech"]
ProviderInvocationActiveState = Literal["prepared", "sent", "accepted"]


@dataclass(frozen=True)
class NewAsset:
    asset_id: str
    filename: str
    media_type: str
    original_path: Path
    parent_folder_id: str | None = None


@dataclass(frozen=True)
class ClaimNextJob:
    claim_owner: str
    lease_seconds: int
    now: int | None = None


@dataclass(frozen=True)
class RenewJobClaim:
    asset_id: str
    claim_owner: str
    lease_seconds: int
    now: int | None = None


@dataclass(frozen=True)
class JobProgressUpdate:
    asset_id: str
    total_chunks: int | None = None
    done_chunks: int | None = None
    failed_chunks: int | None = None
    next_retry_at: int | None = None


@dataclass(frozen=True)
class JobEventCreate:
    asset_id: str
    level: str
    stage: str | None
    message: str
    payload: ErrorRecord | None = None
    created_at: int | None = None


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    asset_id: str
    status: str
    created_at: int
    stage: str | None
    error: ErrorRecord | None
    started_at: int | None
    finished_at: int | None
    progress_total_chunks: int
    progress_done_chunks: int
    progress_failed_chunks: int
    next_retry_at: int | None
    run_attempt: int
    claim_owner: str | None
    claim_expires_at: int | None


@dataclass(frozen=True)
class JobEventRecord:
    id: int
    level: str
    stage: str | None
    message: str
    payload: ErrorRecord | None
    run_attempt: int
    created_at: int


@dataclass(frozen=True)
class JobClaim:
    asset_id: str
    job_id: str
    run_attempt: int
    claim_expires_at: int


@dataclass(frozen=True)
class ClaimRecoverableJobs:
    now: int | None = None
    reservation_seconds: int = 120


@dataclass(frozen=True)
class RecoveryProviderEntry:
    work_item_id: str
    invocation_attempt: int
    prior_run_attempt: int
    expected_state: ProviderInvocationState
    idempotency_key: str | None
    role: Literal["transcription", "diarization"]
    provider_id: str


@dataclass(frozen=True)
class JobOnlyRecoveryCommand:
    job_id: str
    asset_id: str
    prior_run_attempt: int
    phase: RecoveryPhase
    token: str
    entries: tuple[RecoveryProviderEntry, ...] = ()
    kind: Literal["job_only"] = "job_only"


@dataclass(frozen=True)
class ProviderRecoveryCommand:
    job_id: str
    asset_id: str
    prior_run_attempt: int
    phase: RecoveryPhase
    token: str
    entries: tuple[RecoveryProviderEntry, ...]
    kind: Literal["provider_set"] = "provider_set"


RecoveryCommand = JobOnlyRecoveryCommand | ProviderRecoveryCommand


@dataclass(frozen=True)
class RecoveryClaimSet:
    commands: tuple[RecoveryCommand, ...]


@dataclass(frozen=True)
class RecoveryProviderOutcome:
    entry: RecoveryProviderEntry
    kind: Literal["prepared", "cancelled", "abandoned"]
    http_status: int | None

    def __post_init__(self) -> None:
        if self.kind == "prepared":
            if self.entry.expected_state != "prepared" or self.http_status is not None:
                raise ValueError(
                    "prepared outcome requires a prepared entry without an HTTP status"
                )
        elif self.kind == "cancelled":
            if self.entry.expected_state not in {"sent", "accepted"} or self.http_status is None:
                raise ValueError(
                    "cancelled outcome requires a sent or accepted entry and HTTP status"
                )
        elif self.kind == "abandoned":
            if self.entry.expected_state not in {"sent", "accepted"}:
                raise ValueError("abandoned outcome requires a sent or accepted entry")
            if (self.entry.role, self.entry.provider_id) != ("diarization", "senko"):
                raise ValueError("abandoned outcome requires an in-process Senko diarization entry")
            if self.http_status is not None:
                raise ValueError("abandoned outcome has no HTTP status")
        else:
            raise ValueError("recovery outcome kind is unsupported")

    @classmethod
    def prepared(cls, entry: RecoveryProviderEntry) -> "RecoveryProviderOutcome":
        return cls(entry=entry, kind="prepared", http_status=None)

    @classmethod
    def cancelled(
        cls, entry: RecoveryProviderEntry, *, http_status: int
    ) -> "RecoveryProviderOutcome":
        return cls(entry=entry, kind="cancelled", http_status=http_status)

    @classmethod
    def abandoned(cls, entry: RecoveryProviderEntry) -> "RecoveryProviderOutcome":
        return cls(entry=entry, kind="abandoned", http_status=None)


@dataclass(frozen=True)
class CompleteProviderRecovery:
    command: RecoveryCommand
    outcomes: tuple[RecoveryProviderOutcome, ...]
    now: int | None = None

    def __post_init__(self) -> None:
        if any(not isinstance(outcome, RecoveryProviderOutcome) for outcome in self.outcomes):
            raise ValueError("recovery outcomes must contain outcome records")


@dataclass(frozen=True)
class RecoveryCompletion:
    requeued: bool
    reservation_retained: bool


@dataclass(frozen=True)
class PrepareProviderWorkItem:
    work_item_id: str
    job_id: str
    asset_id: str
    role: Literal["transcription", "diarization"]
    chunk_key: str
    run_attempt: int
    idempotency_key: str
    request_hash: str
    provider_id: str = "local"
    image_digest: str = "local"
    work_generation: int = 1
    correlation_id: str = ""

    @classmethod
    def for_transcription(
        cls,
        *,
        work_item_id: str,
        job_id: str,
        asset_id: str,
        chunk_key: str,
        run_attempt: int,
        idempotency_key: str,
        request_hash: str,
        provider_id: str = "local",
        image_digest: str = "local",
        work_generation: int = 1,
        correlation_id: str | None = None,
    ) -> "PrepareProviderWorkItem":
        return cls(
            work_item_id=work_item_id,
            job_id=job_id,
            asset_id=asset_id,
            role="transcription",
            chunk_key=chunk_key,
            run_attempt=run_attempt,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            provider_id=provider_id,
            image_digest=image_digest,
            work_generation=work_generation,
            correlation_id=correlation_id or str(uuid4()),
        )


@dataclass(frozen=True)
class PreparedProviderInvocation:
    work_item_id: str
    invocation_attempt: int
    run_attempt: int
    idempotency_key: str
    request_hash: str
    correlation_id: str
    state: ProviderInvocationState


@dataclass(frozen=True)
class ProviderMetadata:
    mod_id: str | None = None
    mod_version: str | None = None
    mod_image_digest: str | None = None
    runtime: str | None = None
    model_id: str | None = None
    model_revision: str | None = None
    model_sha256: str | None = None
    license_ref: str | None = None
    access_declaration: str | None = None

    def as_dict(self) -> dict[str, str]:
        return {
            name: value
            for name, value in (
                ("mod_id", self.mod_id),
                ("mod_version", self.mod_version),
                ("mod_image_digest", self.mod_image_digest),
                ("runtime", self.runtime),
                ("model_id", self.model_id),
                ("model_revision", self.model_revision),
                ("model_sha256", self.model_sha256),
                ("license_ref", self.license_ref),
                ("access_declaration", self.access_declaration),
            )
            if value is not None
        }


@dataclass(frozen=True)
class ProviderInvocationRecord:
    work_item_id: str
    invocation_attempt: int
    run_attempt: int
    correlation_id: str
    idempotency_key: str
    request_hash: str
    duplicate_recovery: bool
    state: ProviderInvocationState
    prepared_at: int
    sent_at: int | None
    accepted_at: int | None
    completed_at: int | None
    cancelled_at: int | None
    failed_at: int | None
    cancellation_http_status: int | None
    error_category: str | None
    provider_metadata: ProviderMetadata | None
    embedding_space: EmbeddingSpaceV1 | None
    timing_ms: int | None


@dataclass(frozen=True)
class ProviderInvocationTransitionRecord:
    sequence: int
    from_state: ProviderInvocationState | None
    to_state: ProviderInvocationState
    claimant_run_attempt: int
    cancellation_http_status: int | None
    created_at: int


@dataclass(frozen=True)
class ProviderInvocationTransition:
    work_item_id: str
    invocation_attempt: int
    expected_state: ProviderInvocationState
    to_state: ProviderInvocationState
    claimant_run_attempt: int
    cancellation_http_status: int | None = None
    error_category: str | None = None
    provider_metadata: ProviderMetadata | None = None
    embedding_space: EmbeddingSpaceV1 | None = None
    timing_ms: int | None = None

    @classmethod
    def _from_prepared(
        cls, prepared: PreparedProviderInvocation, to_state: ProviderInvocationState
    ) -> "ProviderInvocationTransition":
        return cls(
            work_item_id=prepared.work_item_id,
            invocation_attempt=prepared.invocation_attempt,
            expected_state=prepared.state,
            to_state=to_state,
            claimant_run_attempt=prepared.run_attempt,
        )

    @classmethod
    def sent(cls, prepared: PreparedProviderInvocation) -> "ProviderInvocationTransition":
        return cls._from_prepared(prepared, "sent")

    @classmethod
    def accepted(cls, prepared: PreparedProviderInvocation) -> "ProviderInvocationTransition":
        return cls(
            work_item_id=prepared.work_item_id,
            invocation_attempt=prepared.invocation_attempt,
            expected_state="sent",
            to_state="accepted",
            claimant_run_attempt=prepared.run_attempt,
        )

    @classmethod
    def completed(cls, prepared: PreparedProviderInvocation) -> "ProviderInvocationTransition":
        return cls._from_prepared(prepared, "completed")

    @classmethod
    def failed(cls, prepared: PreparedProviderInvocation) -> "ProviderInvocationTransition":
        return cls._from_prepared(prepared, "failed")

    @classmethod
    def cancelled(cls, prepared: PreparedProviderInvocation) -> "ProviderInvocationTransition":
        return cls._from_prepared(prepared, "cancelled")


@dataclass(frozen=True)
class TransitionResult:
    applied: bool


@dataclass(frozen=True)
class TimedTranscriptUnit:
    """One provider-produced transcript unit on the asset media timeline."""

    unit_index: int
    text: str
    start_ms: int
    end_ms: int
    confidence: float | None
    language: str | None
    token_kind: Literal["word", "token", "punctuation", "other"]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.unit_index, int)
            or isinstance(self.unit_index, bool)
            or self.unit_index < 0
        ):
            raise ValueError("unit_index must be a nonnegative integer")
        if not self.text:
            raise ValueError("timed transcript unit text must be nonempty")
        if (
            not isinstance(self.start_ms, int)
            or not isinstance(self.end_ms, int)
            or isinstance(self.start_ms, bool)
            or isinstance(self.end_ms, bool)
            or self.start_ms < 0
            or self.end_ms < self.start_ms
        ):
            raise ValueError("timed transcript unit bounds are invalid")
        if self.confidence is not None and (
            not isinstance(self.confidence, (int, float))
            or isinstance(self.confidence, bool)
            or not isfinite(self.confidence)
            or not 0 <= self.confidence <= 1
        ):
            raise ValueError("timed transcript unit confidence must be within [0, 1]")


@dataclass(frozen=True)
class PersistedTimedTranscriptUnit:
    asset_id: str
    chunk_index: int
    unit_index: int
    text: str
    start_ms: int
    end_ms: int
    confidence: float | None
    language: str | None
    token_kind: Literal["word", "token", "punctuation", "other"]


@dataclass(frozen=True)
class ReplaceTranscriptTimedUnits:
    asset_id: str
    chunk_index: int
    units: tuple[TimedTranscriptUnit, ...]

    def __post_init__(self) -> None:
        if not self.asset_id:
            raise ValueError("asset_id must be nonempty")
        if (
            not isinstance(self.chunk_index, int)
            or isinstance(self.chunk_index, bool)
            or self.chunk_index < 0
        ):
            raise ValueError("chunk_index must be a nonnegative integer")
        if any(not isinstance(unit, TimedTranscriptUnit) for unit in self.units):
            raise ValueError("timed transcript units must be typed records")
        if [unit.unit_index for unit in self.units] != list(range(len(self.units))):
            raise ValueError("timed transcript unit indexes must be contiguous from zero")


@dataclass(frozen=True)
class CompleteTranscriptionProviderInvocation:
    work_item_id: str
    invocation_attempt: int
    claimant_run_attempt: int
    asset_id: str
    chunk_index: int
    segment: TranscriptSegment
    attempts: int
    provider_metadata: ProviderMetadata | None = None
    timing_ms: int | None = None
    timed_units: tuple[TimedTranscriptUnit, ...] = ()

    def __post_init__(self) -> None:
        ReplaceTranscriptTimedUnits(self.asset_id, self.chunk_index, self.timed_units)


@dataclass(frozen=True)
class RetryProviderInvocation:
    work_item_id: str
    expected_state: ProviderInvocationActiveState
    claimant_run_attempt: int
    correlation_id: str
    error_category: str


@dataclass(frozen=True)
class AssetMove:
    asset_id: str
    parent_folder_id: str | None


@dataclass(frozen=True)
class AssetMoveResult:
    asset_id: str
    parent_folder_id: str | None
    updated_at: int


@dataclass(frozen=True)
class AssetCleanup:
    asset_id: str
    media_path: Path
    exports_path: Path


@dataclass(frozen=True)
class DiarizationMetadata:
    asset_id: str
    wav_path: Path
    duration: float
    diarization_stats: ImmutableJsonObject
    raw_segments: tuple[SpeakerSegment, ...]
    merged_segments: tuple[SpeakerSegment, ...]
    speaker_centroids: SpeakerCentroids
    embedding_space: EmbeddingSpaceV1 | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "diarization_stats", ImmutableJsonObject.from_value(self.diarization_stats)
        )
        object.__setattr__(self, "raw_segments", tuple(self.raw_segments))
        object.__setattr__(self, "merged_segments", tuple(self.merged_segments))
        object.__setattr__(
            self,
            "speaker_centroids",
            SpeakerCentroids.from_value(self.speaker_centroids),
        )


@dataclass(frozen=True)
class CompleteDiarizationProviderInvocation:
    work_item_id: str
    invocation_attempt: int
    claimant_run_attempt: int
    asset_id: str
    metadata: DiarizationMetadata
    provider_metadata: ProviderMetadata | None = None
    timing_ms: int | None = None


@dataclass(frozen=True)
class AssetSummaryUpdate:
    asset_id: str
    status: str
    text: str | None = None
    error: str | None = None
    model: str | None = None
    title: str | None = None


@dataclass(frozen=True)
class TranscriptChunkUpsert:
    asset_id: str
    chunk_index: int
    segment: TranscriptSegment
    attempts: int
    status: str = "success"
    error: ErrorRecord | None = None


@dataclass(frozen=True)
class SpeakerUpsert:
    speaker_id: str
    display_name: str
    centroid: tuple[float, ...]
    sample_count: int
    embedding_space: EmbeddingSpaceV1

    def __post_init__(self) -> None:
        object.__setattr__(self, "centroid", tuple(float(value) for value in self.centroid))


@dataclass(frozen=True)
class SpeakerRelabel:
    asset_id: str
    local_speaker: str
    speaker_id: str
    display_name: str
    similarity: float | None


@dataclass(frozen=True)
class AiSpeakerName:
    local_speaker: str
    display_name: str


@dataclass(frozen=True)
class ApplyAiSpeakerNames:
    asset_id: str
    names: tuple[AiSpeakerName, ...]


@dataclass(frozen=True)
class AppliedAiSpeakerNames:
    names: tuple[AiSpeakerName, ...]


@dataclass(frozen=True)
class CompleteAsset:
    asset_id: str
    metadata: DiarizationMetadata
    transcript_segments: tuple[TranscriptSegment, ...]
    exports: ExportPaths


@dataclass(frozen=True)
class FindProviderWorkItem:
    job_id: str
    asset_id: str
    role: Literal["transcription", "diarization"]
    provider_id: str
    image_digest: str
    chunk_key: str
    work_generation: int


@dataclass(frozen=True)
class FolderCreate:
    name: str
    parent_id: str | None = None


@dataclass(frozen=True)
class FolderMove:
    folder_id: str
    parent_id: str | None


@dataclass(frozen=True)
class FolderRename:
    folder_id: str
    name: str


@dataclass(frozen=True)
class UploadSessionCreate:
    filename: str
    total_size: int
    uploads_dir: Path


@dataclass(frozen=True)
class UploadSessionCompletion:
    upload_id: str
    asset_id: str
    media_type: str
    stored_path: Path
