from dataclasses import dataclass
from pathlib import Path

from stt_vault.core.models.api import JsonValue
from stt_vault.core.models.mod_contracts import EmbeddingSpaceV1
from stt_vault.core.models.records import SpeakerMatch, SpeakerSegment, TranscriptSegment


@dataclass(frozen=True)
class PreparedAsset:
    wav_path: Path
    duration: float
    diarization_stats: dict[str, JsonValue]
    raw_segments: list[SpeakerSegment]
    merged_segments: list[SpeakerSegment]
    speaker_centroids: dict[str, list[float]]
    embedding_space: EmbeddingSpaceV1 | None = None


@dataclass
class TranscriptionWork:
    chunks: list[TranscriptSegment]
    pending_chunks: list[TranscriptSegment]
    completed_chunks: int
    failed_chunks: int = 0


def apply_speaker_names(
    transcript_segments: list[TranscriptSegment],
    speaker_matches: dict[str, SpeakerMatch],
) -> list[TranscriptSegment]:
    enriched: list[TranscriptSegment] = []
    for segment in transcript_segments:
        match = speaker_matches.get(segment.speaker)
        enriched.append(
            TranscriptSegment(
                start=segment.start,
                end=segment.end,
                speaker=segment.speaker,
                text=segment.text,
                chunk_index=segment.chunk_index,
                chunk_start=segment.chunk_start,
                chunk_end=segment.chunk_end,
                attempts=segment.attempts,
                speaker_id=match.speaker_id if match is not None else segment.speaker,
                speaker_name=match.display_name if match is not None else segment.speaker,
                speaker_similarity=match.score if match is not None else None,
            )
        )
    return enriched
