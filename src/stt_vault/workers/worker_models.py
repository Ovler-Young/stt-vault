from dataclasses import dataclass
from pathlib import Path

from stt_vault.core.models.api import JsonValue
from stt_vault.core.models.records import SpeakerMatch, SpeakerSegment, TranscriptSegment


@dataclass(frozen=True)
class PreparedAsset:
    wav_path: Path
    duration: float
    diarization_stats: dict[str, JsonValue]
    raw_segments: list[SpeakerSegment]
    merged_segments: list[SpeakerSegment]
    speaker_centroids: dict[str, list[float]]


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
        match = speaker_matches.get(segment["speaker"], {})
        enriched.append(
            {
                **segment,
                "speaker_id": match.get("speaker_id", segment["speaker"]),
                "speaker_name": match.get("display_name", segment["speaker"]),
                "speaker_similarity": match.get("score"),
            }
        )
    return enriched
