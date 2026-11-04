from typing import NotRequired, TypedDict

from .api import JsonValue


class ErrorRecord(TypedDict, total=False):
    category: str
    message: str


class EventPayload(ErrorRecord, total=False):
    cause: str


class ExportPaths(TypedDict, total=False):
    json: str
    whisper_json: str
    ai_text: str
    srt: str
    vtt: str
    hyperaudio_html: str
    rttm: str
    visual_events: str


class SpeakerSegment(TypedDict):
    start: float
    end: float
    speaker: str


class TranscriptChunk(SpeakerSegment):
    chunk_index: int


class TranscriptSegment(SpeakerSegment):
    text: str
    chunk_start: NotRequired[float]
    chunk_end: NotRequired[float]
    attempts: NotRequired[int]
    speaker_id: NotRequired[str]
    speaker_name: NotRequired[str]
    speaker_similarity: NotRequired[float | None]


class VisualEvent(TypedDict):
    timestamp: float
    score: float
    kind: str


class PersistedVisualEvent(VisualEvent):
    event_index: int
    created_at: int


class SpeakerMatch(TypedDict):
    speaker_id: str
    display_name: str
    score: float | None


class KnownSpeaker(TypedDict):
    id: str
    display_name: str
    centroid: list[float]
    sample_count: int
    created_at: int
    updated_at: int


class AssetRecord(TypedDict, total=False):
    id: str
    filename: str
    title: str | None
    recorded_at: int | None
    media_type: str
    original_path: str
    wav_path: str | None
    duration: float | None
    status: str
    created_at: int
    updated_at: int
    error: ErrorRecord | None
    diarization_stats: dict[str, JsonValue] | None
    exports: ExportPaths
    raw_segments: list[SpeakerSegment]
    merged_segments: list[SpeakerSegment]
    transcript_segments: list[TranscriptSegment]
    speaker_centroids: dict[str, list[float]]
    summary_status: str | None
    summary_text: str | None
    summary_error: str | None
    summary_model: str | None
    summary_updated_at: int | None


class CleanupTask(TypedDict):
    asset_id: str
    media_path: str
    exports_path: str


class AudioStream(TypedDict):
    audio_index: int
    stream_index: int | None
    codec_name: str | None
    channels: int | None
    channel_layout: str | None
    bit_rate: str | None
    language: str | None
    title: str | None


class SpeakerRecord(TypedDict, total=False):
    id: str
    display_name: str
    centroid: list[float]
    sample_count: int
    created_at: int
    updated_at: int


class UploadSessionRecord(TypedDict):
    id: str
    filename: str
    total_size: int
    offset: int
    temp_path: str
    created_at: int
    updated_at: int


class UploadResponse(TypedDict):
    id: str
    filename: str
    size: int
    offset: int
