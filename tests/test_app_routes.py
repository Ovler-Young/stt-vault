from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from stt_vault import db
from stt_vault.app import create_app
from stt_vault.settings import get_settings

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


def test_create_app_registers_current_api_route_table(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app = create_test_app(monkeypatch, tmp_path)

    assert api_route_pairs(app) == EXPECTED_API_ROUTES


def test_public_system_endpoints_do_not_require_admin(client: TestClient) -> None:
    health_response = client.get("/api/health")
    config_response = client.get("/api/config")

    assert health_response.status_code == 200
    assert health_response.json() == {"status": "ok"}
    assert config_response.status_code == 200
    assert config_response.json()["auth_required"] is True


def test_protected_media_gets_require_bearer_token(client: TestClient) -> None:
    missing_response = client.get("/api/assets/missing/media")
    authenticated_response = client.get(
        "/api/assets/missing/media",
        headers=auth_headers(client),
    )

    assert missing_response.status_code == 401
    assert missing_response.json() == {"detail": "Missing bearer token"}
    assert authenticated_response.status_code == 404
    assert authenticated_response.json() == {"detail": "Asset not found"}


def test_mutating_routes_require_bearer_auth(client: TestClient) -> None:
    missing_response = client.post("/api/speakers/recompute")
    authenticated_response = client.post(
        "/api/speakers/recompute",
        headers=auth_headers(client),
    )

    assert missing_response.status_code == 401
    assert authenticated_response.status_code == 200
    assert authenticated_response.json() == {"assets": 0}


def test_batch_upload_isolated_per_file_and_rejects_traversal(client: TestClient) -> None:
    response = client.post(
        "/api/assets/batch",
        headers=auth_headers(client),
        data={"relative_paths": ["recordings/clip.wav", "../escape.wav"]},
        files=[
            ("files", ("clip.wav", b"audio", "audio/wav")),
            ("files", ("escape.wav", b"x", "audio/wav")),
        ],
    )

    assert response.status_code == 200
    assert response.json()["results"][0]["status"] == "queued"
    assert response.json()["results"][1] == {
        "path": "../escape.wav",
        "status": "failed",
        "detail": "Relative path is invalid",
    }


def test_ranged_upload_tracks_offset_and_completes_asset(client: TestClient) -> None:
    headers = auth_headers(client)
    create_response = client.post(
        "/api/uploads",
        headers=headers,
        json={"filename": "2026-07-15_12-57-52.mp4", "size": 10},
    )

    assert create_response.status_code == 200
    upload_id = create_response.json()["id"]
    upload = db.get_upload_session(get_settings().stt_db_path, upload_id)
    assert upload is not None
    Path(upload["temp_path"]).write_bytes(b"unconfirmed")
    first_response = client.put(
        f"/api/uploads/{upload_id}",
        headers={**headers, "Content-Range": "bytes 0-4/10"},
        content=b"first",
    )
    rejected_response = client.put(
        f"/api/uploads/{upload_id}",
        headers={**headers, "Content-Range": "bytes 7-9/10"},
        content=b"bad",
    )
    short_response = client.put(
        f"/api/uploads/{upload_id}",
        headers={**headers, "Content-Range": "bytes 5-9/10"},
        content=b"no",
    )
    status_response = client.get(f"/api/uploads/{upload_id}", headers=headers)

    assert first_response.status_code == 200
    assert first_response.json()["offset"] == 5
    assert rejected_response.status_code == 409
    assert rejected_response.json() == {"detail": "Expected range to start at byte 5"}
    assert short_response.status_code == 400
    assert short_response.json() == {"detail": "Content-Range does not match body size"}
    assert status_response.json()["offset"] == 5

    final_response = client.put(
        f"/api/uploads/{upload_id}",
        headers={**headers, "Content-Range": "bytes 5-9/10"},
        content=b"final",
    )
    complete_response = client.post(f"/api/uploads/{upload_id}/complete", headers=headers)

    assert final_response.status_code == 200
    assert final_response.json()["offset"] == 10
    assert complete_response.status_code == 200
    asset = db.get_asset(get_settings().stt_db_path, complete_response.json()["id"])
    assert asset is not None
    assert asset["filename"] == "2026-07-15_12-57-52.mp4"
    assert asset["recorded_at"] == 1_784_120_272
    assert Path(asset["original_path"]).read_bytes() == b"firstfinal"
    assert client.get(f"/api/uploads/{upload_id}", headers=headers).status_code == 404


def test_summary_requires_completed_transcript(client: TestClient) -> None:
    response = client.post("/api/assets/missing/summary", headers=auth_headers(client))
    assert response.status_code == 404


def test_summary_uses_complete_context_and_only_applies_confident_speaker_names(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
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

    monkeypatch.setattr("stt_vault.summary_service.OpenAI", FakeOpenAI)
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

    response = client.post("/api/assets/asset-1/summary", headers=auth_headers(client))
    asset = db.get_asset(db_path, "asset-1")

    assert response.status_code == 200
    assert response.json()["title"] == "Friday release approved"
    assert response.json()["speaker_names"] == {"SPEAKER_00": "Maya Chen"}
    assert "response_format" not in completions.calls[0]
    assert "[SPEAKER_00 00:00-00:02] Ship Friday." in completions.calls[0]["messages"][1]["content"]
    assert "[SPEAKER_01 (Alice) 00:02-00:04] I approve." in completions.calls[0][
        "messages"
    ][1]["content"]
    assert asset is not None
    assert asset["title"] == "Friday release approved"
    assert "## Summary\n\nThe team approved a Friday release." in asset["summary_text"]
    assert "## Highlights\n\n- [00:00:03] Friday release confirmed." in asset["summary_text"]
    assert [segment["speaker_name"] for segment in asset["transcript_segments"]] == [
        "Maya Chen",
        "Alice",
    ]
