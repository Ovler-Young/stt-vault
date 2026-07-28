from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from stt_vault.core.app import create_app
from stt_vault.core.config import get_settings
from stt_vault.core.models.api import AssetResponse
from stt_vault.persistence import db

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


def test_asset_response_rejects_malformed_database_rows() -> None:
    with pytest.raises(ValidationError):
        AssetResponse.model_validate({"id": "asset-1", "filename": "clip.wav"})


def test_asset_response_rejects_unknown_database_fields() -> None:
    with pytest.raises(ValidationError):
        AssetResponse.model_validate(
            {
                "id": "asset-1",
                "filename": "clip.wav",
                "media_type": "audio",
                "status": "queued",
                "created_at": 1,
                "updated_at": 1,
                "unexpected": "unvalidated",
            }
        )


def test_asset_api_does_not_expose_persisted_secret_or_path(client: TestClient) -> None:
    upload = client.post(
        "/api/assets",
        headers=auth_headers(client),
        files={"file": ("clip.wav", b"audio", "audio/wav")},
    )
    asset_id = upload.json()["id"]
    db.mark_failed(
        get_settings().stt_db_path,
        asset_id,
        {"category": "provider", "message": "Bearer api-token /srv/private/clip.wav"},
    )

    response = client.get(f"/api/assets/{asset_id}", headers=auth_headers(client))

    assert response.status_code == 200
    assert "api-token" not in response.text
    assert "/srv/private/clip.wav" not in response.text


def test_asset_events_uses_the_dedicated_event_query(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = get_settings().stt_db_path
    db.create_asset(db_path, "asset-1", "clip.wav", "audio", db_path.parent / "clip.wav")
    db.add_event(db_path, "asset-1", "info", "queued", "Job queued")

    def fail_if_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("event history must not load the asset aggregate")

    monkeypatch.setattr("stt_vault.routes.assets.details.db.get_asset", fail_if_called)

    response = client.get("/api/assets/asset-1/events", headers=auth_headers(client))

    assert response.status_code == 200
    assert [event["message"] for event in response.json()] == ["Job queued"]


def test_asset_detail_defaults_to_event_history_and_supports_lean_reads(client: TestClient) -> None:
    db_path = get_settings().stt_db_path
    db.create_asset(db_path, "asset-1", "clip.wav", "audio", db_path.parent / "clip.wav")
    db.add_event(db_path, "asset-1", "info", "queued", "Job queued")
    headers = auth_headers(client)

    legacy_response = client.get("/api/assets/asset-1", headers=headers)
    lean_response = client.get("/api/assets/asset-1?include_event_history=false", headers=headers)

    assert legacy_response.status_code == 200
    assert [event["message"] for event in legacy_response.json()["event_history"]] == ["Job queued"]
    assert lean_response.status_code == 200
    assert lean_response.json()["event_history"] is None


def test_asset_events_returns_not_found_for_missing_asset(client: TestClient) -> None:
    response = client.get("/api/assets/missing/events", headers=auth_headers(client))

    assert response.status_code == 404
    assert response.json() == {"detail": "Asset not found"}


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
