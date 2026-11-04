from types import SimpleNamespace

from stt_vault.processing.summary_service import generate_asset_summary


def test_summary_generation_uses_injected_repository() -> None:
    calls: list[tuple[str, object]] = []

    class FakeRepository:
        def get_asset(self, asset_id: str):
            calls.append(("get", asset_id))
            return {
                "status": "success",
                "transcript_segments": [
                    {"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00", "text": "Hello"}
                ],
            }

        def update_asset_summary(self, asset_id: str, **kwargs):
            calls.append(("summary", (asset_id, kwargs)))

        def apply_ai_speaker_names(self, asset_id: str, speaker_names: dict[str, str]):
            calls.append(("speakers", (asset_id, speaker_names)))
            return speaker_names

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
        repository=FakeRepository(),
        client_factory=lambda **_kwargs: SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions())
        ),
    )

    assert result["title"] == "Hello"
    assert [name for name, _payload in calls] == ["get", "summary", "speakers", "summary"]
