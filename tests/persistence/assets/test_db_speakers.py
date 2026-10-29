from pathlib import Path

from _support.db_assets import create_processing_asset, initialized_db

from stt_vault.core.models.mod_contracts import EmbeddingSpaceV1
from stt_vault.core.models.records import (
    AiSpeakerName,
    ApplyAiSpeakerNames,
    CompleteAsset,
    DiarizationMetadata,
    ExportPaths,
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


def test_speaker_edits_preserve_split_display_and_raw_requests(tmp_path: Path) -> None:
    database = create_processing_asset(tmp_path)
    database.upsert_speaker(SpeakerUpsert("speaker-a", "Alice", [1, 2], 1, EMBEDDING_SPACE))
    database.upsert_speaker(SpeakerUpsert("speaker-b", "Bob", [2, 1], 1, EMBEDDING_SPACE))
    request = TranscriptSegment(0, 8, "UNKNOWN", "hello there", 0, 0, 8)
    database.upsert_transcript_chunk(TranscriptChunkUpsert("asset-1", 0, request, 1))
    database.complete_asset(
        CompleteAsset(
            "asset-1",
            DiarizationMetadata("asset-1", tmp_path / "audio.wav", 8, {}, [], [], {}),
            (
                TranscriptSegment(
                    0,
                    4,
                    "SPEAKER_00",
                    "hello ",
                    0,
                    0,
                    8,
                    speaker_id="speaker-a",
                    speaker_name="Alice",
                ),
                TranscriptSegment(
                    4, 8, "SPEAKER_01", "there", 0, 0, 8, speaker_id="speaker-b", speaker_name="Bob"
                ),
            ),
            ExportPaths(),
        )
    )
    database.rename_speaker("speaker-a", "Alicia")
    assert database.get_asset("asset-1").transcript_segments[0].speaker_name == "Alicia"
    assert database.list_asset_ids_for_speaker("speaker-b") == ["asset-1"]
    database.merge_speakers("speaker-b", "speaker-a")
    assert {s.speaker_id for s in database.get_asset("asset-1").transcript_segments} == {
        "speaker-a"
    }
    database.relabel_asset_speaker(
        SpeakerRelabel("asset-1", "SPEAKER_01", "speaker-c", "Carol", 0.9)
    )
    assert database.list_asset_ids_for_speaker("speaker-c") == ["asset-1"]
    database.delete_speaker("speaker-a")
    display = database.get_asset("asset-1").transcript_segments
    assert [(s.speaker_id, s.speaker_name, s.text) for s in display] == [
        ("SPEAKER_00", "SPEAKER_00", "hello "),
        ("speaker-c", "Carol", "there"),
    ]
    raw = database.list_transcript_chunks("asset-1")
    assert [(s.speaker, s.text, s.start, s.end) for s in raw] == [("UNKNOWN", "hello there", 0, 8)]
