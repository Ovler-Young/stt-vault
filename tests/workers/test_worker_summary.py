from types import SimpleNamespace

from stt_vault.core.models.records import (
    AppliedAiSpeakerNames,
    AssetRecord,
    AssetSummaryUpdate,
    TranscriptSegment,
)
from stt_vault.processing.summary_service import generate_asset_summary


def test_summary_generation_uses_injected_database() -> None:
    calls: list[tuple[str, object]] = []

    class FakeDatabase:
        def get_asset(self, asset_id: str):
            calls.append(("get", asset_id))
            return AssetRecord(
                asset_id,
                "clip.wav",
                "audio",
                "/tmp/clip.wav",
                "success",
                1,
                1,
                transcript_segments=(TranscriptSegment(0.0, 1.0, "SPEAKER_00", "Hello"),),
            )

        def update_asset_summary(self, command: AssetSummaryUpdate) -> None:
            calls.append(("summary", command))

        def apply_speaker_name_updates(self, command):
            calls.append(("speakers", command))
            return AppliedAiSpeakerNames(())

    class FakeCompletions:
        def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content='{"title":"Hello","content_summary":"Greeting","highlights":[]}'
                        )
                    )
                ]
            )

    settings = SimpleNamespace(
        openai_speaker_name_confidence=0.9,
        openai_api_key="",
        openai_base_url="",
        openai_summary_model="model",
    )
    result = generate_asset_summary(
        settings,
        "asset-1",
        database=FakeDatabase(),
        client_factory=lambda **_kwargs: SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions())
        ),
    )

    assert result["title"] == "Hello"
    assert [name for name, _payload in calls] == ["get", "summary", "speakers", "summary"]
