from pathlib import Path

from _support.db_assets import create_processing_asset, initialized_db

from stt_vault.core.models.mod_contracts import EmbeddingSpaceV1
from stt_vault.core.models.records import (
    AiSpeakerName,
    ApplyAiSpeakerNames,
    NewAsset,
    SpeakerRelabel,
    SpeakerUpsert,
    TranscriptChunkUpsert,
    TranscriptSegment,
)

EMBEDDING_SPACE = EmbeddingSpaceV1(
    space_id="test-space",
    model_id="test-model",
    revision="r1",
    sha256="a" * 64,
    dimension=2,
    metric="cosine",
)


def test_speaker_operations_propagate_to_transcript_chunks(tmp_path: Path) -> None:
    database = create_processing_asset(tmp_path)
    database.upsert_speaker(SpeakerUpsert("speaker-a", "Alice", [1.0, 3.0], 2, EMBEDDING_SPACE))
    database.upsert_speaker(SpeakerUpsert("speaker-a", "Alice", [3.0, 5.0], 2, EMBEDDING_SPACE))
    database.upsert_speaker(SpeakerUpsert("speaker-b", "Bob", [5.0, 7.0], 1, EMBEDDING_SPACE))
    database.upsert_transcript_chunk(
        TranscriptChunkUpsert(
            "asset-1",
            0,
            TranscriptSegment(
                0.0,
                4.0,
                "SPEAKER_00",
                "hello",
                speaker_id="speaker-a",
                speaker_name="Alice",
                speaker_similarity=0.8,
            ),
            1,
        )
    )
    database.upsert_transcript_chunk(
        TranscriptChunkUpsert(
            "asset-1",
            1,
            TranscriptSegment(
                5.0,
                8.0,
                "SPEAKER_01",
                "there",
                speaker_id="speaker-b",
                speaker_name="Bob",
                speaker_similarity=0.6,
            ),
            1,
        )
    )

    speaker = database.get_speaker("speaker-a")
    assert speaker is not None
    assert speaker.centroid == (2.0, 4.0)
    assert speaker.sample_count == 4
    matched = database.find_speaker_by_display_name("alice")
    assert matched is not None
    assert matched.id == "speaker-a"

    database.rename_speaker("speaker-a", "Alicia")
    database.relabel_asset_speaker(
        SpeakerRelabel("asset-1", "SPEAKER_01", "speaker-a", "Alicia", 0.91)
    )
    database.merge_speakers("speaker-b", "speaker-a")
    assert {item.speaker_id for item in database.list_transcript_chunks("asset-1")} == {"speaker-a"}

    database.delete_speaker("speaker-a")
    assert [item.speaker_id for item in database.list_transcript_chunks("asset-1")] == [
        "SPEAKER_00",
        "SPEAKER_01",
    ]


def test_ai_speaker_names_only_replace_local_labels(tmp_path: Path) -> None:
    database = initialized_db(tmp_path)
    database.create_asset(NewAsset("asset-1", "clip.mp4", "video", tmp_path / "clip.mp4"))
    database.upsert_transcript_chunk(
        TranscriptChunkUpsert("asset-1", 0, TranscriptSegment(0.0, 3.0, "SPEAKER_00", "Welcome"), 1)
    )
    applied = database.apply_speaker_name_updates(
        ApplyAiSpeakerNames("asset-1", (AiSpeakerName("SPEAKER_00", "Maya Chen"),))
    )
    assert applied.names == (AiSpeakerName("SPEAKER_00", "Maya Chen"),)
    assert database.list_transcript_chunks("asset-1")[0].speaker_name == "Maya Chen"
