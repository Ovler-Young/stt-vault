from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from stt_vault.core.app import ApplicationDependencies, create_app
from stt_vault.core.config import Settings, get_settings

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


def test_create_app_accepts_injected_composition_dependencies(tmp_path: Path) -> None:
    settings = Settings(stt_data_dir=tmp_path / "data", stt_db_path=tmp_path / "app.sqlite3")
    calls: list[str] = []
    worker = SimpleNamespace(
        start=lambda: calls.append("start"),
        stop=lambda: calls.append("stop"),
    )
    dependencies = ApplicationDependencies(
        configure_logging=lambda: calls.append("logging"),
        get_settings=lambda: settings,
        prepare_directories=lambda _settings: calls.append("directories"),
        initialize_database=lambda _path: calls.append("initialize"),
        recover_expired_jobs=lambda _path: calls.append("recover"),
        worker_factory=lambda _settings: worker,
        register_routes=lambda _app, _settings, created_worker: (
            calls.append("routes"),
            assert_worker(created_worker, worker),
        ),
        mount_frontend=lambda _app: calls.append("frontend"),
    )

    app = create_app(dependencies)
    with TestClient(app):
        pass

    assert calls == [
        "logging",
        "directories",
        "initialize",
        "recover",
        "routes",
        "frontend",
        "start",
        "stop",
    ]


def assert_worker(actual: object, expected: object) -> None:
    assert actual is expected


def test_run_preserves_structured_logging_for_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    settings = SimpleNamespace(app_host="127.0.0.1", app_port=8099)
    monkeypatch.setattr("stt_vault.core.app.configure_logging", lambda: calls.append("logging"))
    monkeypatch.setattr("stt_vault.core.app.get_settings", lambda: settings)
    monkeypatch.setattr(
        "stt_vault.core.app.uvicorn.run",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    from stt_vault.core.app import run

    run()

    assert calls == [
        "logging",
        (
            ("stt_vault.core.app:create_app",),
            {"factory": True, "host": "127.0.0.1", "port": 8099, "log_config": None},
        ),
    ]


def test_public_system_endpoints_do_not_require_admin(client: TestClient) -> None:
    health_response = client.get("/api/health")
    config_response = client.get("/api/config")

    assert health_response.status_code == 200
    assert health_response.json() == {"status": "ok"}
    assert config_response.status_code == 200
    assert config_response.json() == {
        "auth_required": True,
        "transcribe_model": "gpt-4o-transcribe",
        "senko_device": "auto",
        "batched_embeddings_requested": True,
    }


def test_system_and_summary_routes_publish_named_response_schemas(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    schema = create_test_app(monkeypatch, tmp_path).openapi()

    assert schema["paths"]["/api/config"]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/ConfigResponse"}
    assert schema["paths"]["/api/auth/token"]["post"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/AuthTokenResponse"}
    assert schema["paths"]["/api/assets/{asset_id}/summary"]["post"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/AssetSummaryResponse"}
