from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from stt_vault.core.app import create_app
from stt_vault.core.config import get_settings
from stt_vault.core.models.records import (
    AssetRecord,
    CompleteAsset,
    DiarizationMetadata,
    ExportPaths,
    NewAsset,
    TranscriptSegment,
)
from stt_vault.persistence.sqlite_database import SqliteDatabase
from stt_vault.processing.summary_service import (
    CompletedTranscriptRequiredError,
    generate_asset_summary,
    require_completed_transcript,
)

JWT_SECRET = "test-jwt-secret-that-is-long-enough-for-hs256-signing"


def create_test_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("STT_DATA_DIR", str(data_dir))
    monkeypatch.setenv("STT_DB_PATH", str(data_dir / "app.sqlite3"))
    monkeypatch.setenv("ADMIN_PASSWORD", "secret")
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    get_settings.cache_clear()
    return create_app()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    test_client = TestClient(create_test_app(monkeypatch, tmp_path))
    try:
        yield test_client
    finally:
        test_client.close()
        get_settings.cache_clear()


def auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/api/auth/token", json={"password": "secret"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def database() -> SqliteDatabase:
    return SqliteDatabase(get_settings().stt_db_path)


def test_summary_requires_completed_transcript(client: TestClient) -> None:
    response = client.post("/api/assets/missing/summary", headers=auth_headers(client))
    assert response.status_code == 404


def test_completed_transcript_precondition_is_shared_by_service_and_endpoint(
    client: TestClient,
) -> None:
    asset = AssetRecord("asset-1", "clip.wav", "audio", "/tmp/clip.wav", "processing", 1, 1)

    with pytest.raises(CompletedTranscriptRequiredError):
        require_completed_transcript(asset)

    db_path = get_settings().stt_db_path
    database().create_asset(
        NewAsset("incomplete", "clip.wav", "audio", db_path.parent / "clip.wav")
    )
    response = client.post("/api/assets/incomplete/summary", headers=auth_headers(client))

    assert response.status_code == 409
    assert response.json() == {"detail": "A completed transcript is required"}


def test_summary_endpoint_preserves_valid_generated_response(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset = AssetRecord(
        "asset-1",
        "clip.wav",
        "audio",
        "/tmp/clip.wav",
        "success",
        1,
        1,
        transcript_segments=(TranscriptSegment(0.0, 1.0, "SPEAKER_00", "Complete transcript"),),
    )
    expected = {
        "status": "success",
        "summary": "Meeting summary",
        "title": "Release planning",
        "speaker_names": {"SPEAKER_00": "Maya Chen"},
    }
    monkeypatch.setattr(
        "stt_vault.routes.assets.details.get_asset_or_404", lambda *_args, **_kwargs: asset
    )
    monkeypatch.setattr(
        "stt_vault.routes.assets.details.generate_asset_summary", lambda *_args, **_kwargs: expected
    )

    response = client.post("/api/assets/asset-1/summary", headers=auth_headers(client))

    assert response.status_code == 200
    assert response.json() == expected


@pytest.mark.parametrize(
    "generated_summary",
    [
        pytest.param(
            {"status": "success", "summary": "Meeting summary"},
            id="missing-required-fields",
        ),
        pytest.param(
            {
                "status": "success",
                "summary": "Meeting summary",
                "title": "Release planning",
                "speaker_names": {"SPEAKER_00": "Maya Chen"},
                "unexpected": "field",
            },
            id="unexpected-extra-field",
        ),
    ],
)
def test_summary_endpoint_rejects_malformed_generated_response(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    generated_summary: dict[str, object],
) -> None:
    asset = AssetRecord(
        "asset-1",
        "clip.wav",
        "audio",
        "/tmp/clip.wav",
        "success",
        1,
        1,
        transcript_segments=(TranscriptSegment(0.0, 1.0, "SPEAKER_00", "Complete transcript"),),
    )
    monkeypatch.setattr(
        "stt_vault.routes.assets.details.get_asset_or_404", lambda *_args, **_kwargs: asset
    )
    monkeypatch.setattr(
        "stt_vault.routes.assets.details.generate_asset_summary",
        lambda *_args, **_kwargs: generated_summary,
    )

    response = client.post("/api/assets/asset-1/summary", headers=auth_headers(client))

    assert response.status_code == 502
    assert response.json() == {"detail": "Summary generation failed"}


def test_summary_endpoint_logs_bounded_diagnostic_context(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    asset = AssetRecord(
        "asset-1",
        "clip.wav",
        "audio",
        "/tmp/clip.wav",
        "success",
        1,
        1,
        transcript_segments=(TranscriptSegment(0.0, 1.0, "SPEAKER_00", "Complete transcript"),),
    )
    monkeypatch.setattr(
        "stt_vault.routes.assets.details.get_asset_or_404", lambda *_args, **_kwargs: asset
    )
    monkeypatch.setattr(
        "stt_vault.routes.assets.details.generate_asset_summary",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("provider secret")),
    )

    with caplog.at_level("ERROR"):
        response = client.post("/api/assets/asset-1/summary", headers=auth_headers(client))

    assert response.status_code == 502
    record = next(record for record in caplog.records if record.name.endswith("details"))
    assert record.event_name == "assets.summary_generation_failed"
    assert record.asset_id == "asset-1"
    assert record.cause == "provider secret"


def test_summary_uses_complete_context_and_only_applies_confident_speaker_names(
    client: TestClient,
) -> None:
    class FakeCompletions:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=(
                                '{"title":"Friday release approved",'
                                '"content_summary":"The team approved a Friday release.",'
                                '"themes":["release planning"],"conclusions":[],"decisions":[],'
                                '"action_items":[],"open_questions":[],'
                                '"highlights":[{"timestamp":3,"text":"Friday release confirmed."}],'
                                '"speaker_candidates":['
                                '{"speaker":"SPEAKER_00","name":"Maya Chen",'
                                '"confidence":0.97},'
                                '{"speaker":"SPEAKER_01","name":"Jordan Lee",'
                                '"confidence":0.90}]}'
                            )
                        )
                    )
                ]
            )

    completions = FakeCompletions()

    class FakeOpenAI:
        def __init__(self, **_kwargs) -> None:
            self.chat = SimpleNamespace(completions=completions)

    db_path = get_settings().stt_db_path
    test_database = database()
    test_database.create_asset(
        NewAsset("asset-1", "clip.mp4", "video", db_path.parent / "clip.mp4")
    )
    test_database.complete_asset(
        CompleteAsset(
            "asset-1",
            DiarizationMetadata("asset-1", db_path.parent / "clip.wav", 4.0, {}, [], [], {}),
            (
                TranscriptSegment(0.0, 2.0, "SPEAKER_00", "Ship Friday."),
                TranscriptSegment(2.0, 4.0, "SPEAKER_01", "I approve.", speaker_name="Alice"),
            ),
            ExportPaths(),
        )
    )

    result = generate_asset_summary(
        get_settings(),
        "asset-1",
        database=test_database,
        client_factory=FakeOpenAI,
    )
    asset = test_database.get_asset("asset-1")

    assert result["title"] == "Friday release approved"
    assert result["speaker_names"] == {"SPEAKER_00": "Maya Chen"}
    assert "response_format" not in completions.calls[0]
    assert "[SPEAKER_00 00:00-00:02] Ship Friday." in completions.calls[0]["messages"][1]["content"]
    assert (
        "[SPEAKER_01 (Alice) 00:02-00:04] I approve."
        in completions.calls[0]["messages"][1]["content"]
    )
    assert asset is not None
    assert asset.title == "Friday release approved"
    assert "## Summary\n\nThe team approved a Friday release." in (asset.summary_text or "")
    assert "## Highlights\n\n- [00:00:03] Friday release confirmed." in (asset.summary_text or "")
    assert [segment.speaker_name for segment in asset.transcript_segments] == [
        "Maya Chen",
        "Alice",
    ]
