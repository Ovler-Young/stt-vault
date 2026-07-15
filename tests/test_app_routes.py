from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from stt_vault.app import create_app
from stt_vault.settings import get_settings

EXPECTED_API_ROUTES = [
    ("GET", "/api/health"),
    ("GET", "/api/config"),
    ("POST", "/api/auth/token"),
    ("POST", "/api/assets"),
    ("GET", "/api/assets"),
    ("GET", "/api/jobs"),
    ("GET", "/api/speakers"),
    ("PUT", "/api/speakers/{speaker_id}"),
    ("DELETE", "/api/speakers/{speaker_id}"),
    ("POST", "/api/speakers/{target_speaker_id}/merge"),
    ("POST", "/api/speakers/recompute"),
    ("GET", "/api/assets/{asset_id}"),
    ("POST", "/api/assets/{asset_id}/speakers/{local_speaker}"),
    ("POST", "/api/assets/{asset_id}/speaker-matches/recompute"),
    ("GET", "/api/assets/{asset_id}/events"),
    ("GET", "/api/assets/{asset_id}/visual-events"),
    ("POST", "/api/assets/{asset_id}/visual-events"),
    ("GET", "/api/assets/{asset_id}/visual-events/{event_index}/thumbnail"),
    ("POST", "/api/assets/{asset_id}/retry"),
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
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-that-is-long-enough-for-hs256-signing")
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


def test_protected_media_gets_reject_missing_bearer_token(client: TestClient) -> None:
    response = client.get("/api/assets/missing/media")

    assert response.status_code == 401
    assert response.json() == {"detail": "Missing bearer token"}
