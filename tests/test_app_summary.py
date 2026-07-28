from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from stt_vault.core.app import create_app
from stt_vault.core.settings import get_settings
from stt_vault.persistence import db
from stt_vault.processing.summary_service import (
    CompletedTranscriptRequiredError,
    generate_asset_summary,
    require_completed_transcript,
)
from stt_vault.services.upload_sessions import UploadSessionService

JWT_SECRET = "test-jwt-secret-that-is-long-enough-for-hs256-signing"

EXPECTED_API_ROUTES = [
    ("GET", "/api/health"),
    ("GET", "/api/config"),
    ("POST", "/api/auth/token"),
    ("POST", "/api/assets"),
    ("POST", "/api/assets/batch"),
    ("GET", "/api/assets"),
    ("GET", "/api/jobs"),
    ("POST", "/api/uploads"),
    ("GET", "/api/uploads/{upload_id}"),
    ("PUT", "/api/uploads/{upload_id}"),
    ("POST", "/api/uploads/{upload_id}/complete"),
    ("GET", "/api/folders"),
    ("POST", "/api/folders"),
    ("POST", "/api/folders/{folder_id}/move"),
    ("PUT", "/api/folders/{folder_id}"),
    ("DELETE", "/api/folders/{folder_id}"),
    ("GET", "/api/speakers"),
    ("PUT", "/api/speakers/{speaker_id}"),
    ("DELETE", "/api/speakers/{speaker_id}"),
    ("POST", "/api/speakers/{target_speaker_id}/merge"),
    ("POST", "/api/speakers/recompute"),
    ("GET", "/api/assets/{asset_id}"),
    ("POST", "/api/assets/{asset_id}/summary"),
    ("POST", "/api/assets/{asset_id}/speakers/{local_speaker}"),
    ("POST", "/api/assets/{asset_id}/speaker-matches/recompute"),
    ("GET", "/api/assets/{asset_id}/events"),
    ("GET", "/api/assets/{asset_id}/visual-events"),
    ("POST", "/api/assets/{asset_id}/visual-events"),
    ("GET", "/api/assets/{asset_id}/visual-events/{event_index}/thumbnail"),
    ("POST", "/api/assets/{asset_id}/retry"),
    ("POST", "/api/assets/{asset_id}/move"),
    ("POST", "/api/assets/{asset_id}/cleanup"),
    ("GET", "/api/assets/{asset_id}/audio-tracks"),
    ("GET", "/api/assets/{asset_id}/media"),
    ("GET", "/api/assets/{asset_id}/exports/{format_name}"),
    ("DELETE", "/api/assets/{asset_id}"),
]


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


def api_route_pairs(app) -> list[tuple[str, str]]:
    pairs = []
    routes = list(app.routes)
    for route in routes:
        original_router = getattr(route, "original_router", None)
        if original_router is not None:
            routes.extend(original_router.routes)
            continue
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/"):
            continue
        for method in sorted(route.methods or []):
            if method != "HEAD":
                pairs.append((method, route.path))
    return pairs


def test_summary_requires_completed_transcript(client: TestClient) -> None:
    response = client.post("/api/assets/missing/summary", headers=auth_headers(client))
    assert response.status_code == 404


def test_completed_transcript_precondition_is_shared_by_service_and_endpoint(
    client: TestClient,
) -> None:
    asset = {"status": "processing", "transcript_segments": []}

    with pytest.raises(CompletedTranscriptRequiredError):
        require_completed_transcript(asset)

    db_path = get_settings().stt_db_path
    db.create_asset(db_path, "incomplete", "clip.wav", "audio", db_path.parent / "clip.wav")
    response = client.post("/api/assets/incomplete/summary", headers=auth_headers(client))

    assert response.status_code == 409
    assert response.json() == {"detail": "A completed transcript is required"}


def test_summary_endpoint_preserves_valid_generated_response(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset = {"status": "success", "transcript_segments": [{"text": "Complete transcript"}]}
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
    asset = {"status": "success", "transcript_segments": [{"text": "Complete transcript"}]}
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


def test_upload_session_completion_restores_temp_file_when_database_write_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = SimpleNamespace(
        stt_db_path=tmp_path / "app.sqlite3",
        uploads_dir=tmp_path / "uploads",
        media_dir=tmp_path / "media",
    )
    temp_path = settings.uploads_dir / "upload.part"
    temp_path.parent.mkdir(parents=True)
    temp_path.write_bytes(b"upload")
    upload = {
        "id": "upload-1",
        "filename": "clip.wav",
        "total_size": 6,
        "offset": 6,
        "temp_path": str(temp_path),
    }
    stored_path = settings.media_dir / "asset-1" / "original.wav"
    stored_path.parent.mkdir(parents=True)

    monkeypatch.setattr(
        "stt_vault.services.upload_sessions.get_upload_session", lambda *_args: upload
    )
    monkeypatch.setattr(
        "stt_vault.services.upload_sessions.move_upload",
        lambda *_args: ("asset-1", stored_path, "audio"),
    )
    monkeypatch.setattr(
        "stt_vault.services.upload_sessions.complete_upload_session",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )
    stored_path.write_bytes(b"upload")

    with pytest.raises(RuntimeError, match="database unavailable"):
        UploadSessionService(settings).complete("upload-1")

    assert temp_path.read_bytes() == b"upload"
    assert not (settings.media_dir / "asset-1").exists()


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
    db.create_asset(db_path, "asset-1", "clip.mp4", "video", db_path.parent / "clip.mp4")
    db.upsert_transcript_chunk(
        db_path,
        "asset-1",
        0,
        {"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00", "text": "Ship Friday."},
        attempts=1,
    )
    db.upsert_transcript_chunk(
        db_path,
        "asset-1",
        1,
        {
            "start": 2.0,
            "end": 4.0,
            "speaker": "SPEAKER_01",
            "speaker_name": "Alice",
            "text": "I approve.",
        },
        attempts=1,
    )
    with db.transaction(db_path) as conn:
        conn.execute("UPDATE assets SET status = 'success' WHERE id = 'asset-1'")

    result = generate_asset_summary(
        get_settings(),
        "asset-1",
        client_factory=FakeOpenAI,
    )
    asset = db.get_asset(db_path, "asset-1")

    assert result["title"] == "Friday release approved"
    assert result["speaker_names"] == {"SPEAKER_00": "Maya Chen"}
    assert "response_format" not in completions.calls[0]
    assert "[SPEAKER_00 00:00-00:02] Ship Friday." in completions.calls[0]["messages"][1]["content"]
    assert (
        "[SPEAKER_01 (Alice) 00:02-00:04] I approve."
        in completions.calls[0]["messages"][1]["content"]
    )
    assert asset is not None
    assert asset["title"] == "Friday release approved"
    assert "## Summary\n\nThe team approved a Friday release." in asset["summary_text"]
    assert "## Highlights\n\n- [00:00:03] Friday release confirmed." in asset["summary_text"]
    assert [segment["speaker_name"] for segment in asset["transcript_segments"]] == [
        "Maya Chen",
        "Alice",
    ]
