from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stt_vault.app import create_app
from stt_vault.settings import get_settings

ADMIN_COOKIE_NAME = "stt-vault-admin-password"


def create_test_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    admin_password: str = "secret",
) -> TestClient:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("STT_DATA_DIR", str(data_dir))
    monkeypatch.setenv("STT_DB_PATH", str(data_dir / "app.sqlite3"))
    monkeypatch.setenv("ADMIN_PASSWORD", admin_password)
    get_settings.cache_clear()
    return TestClient(create_app())


@pytest.fixture
def authed_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    client = create_test_client(monkeypatch, tmp_path)
    try:
        yield client
    finally:
        client.close()
        get_settings.cache_clear()


def test_header_auth_still_allows_api_requests(authed_client: TestClient) -> None:
    response = authed_client.get(
        "/api/assets",
        headers={"X-STT-Admin-Password": "secret"},
    )

    assert response.status_code == 200
    assert response.json() == []


def test_cookie_auth_allows_protected_get_requests(authed_client: TestClient) -> None:
    authed_client.cookies.set(ADMIN_COOKIE_NAME, "secret")

    response = authed_client.get("/api/assets")

    assert response.status_code == 200
    assert response.json() == []


def test_encoded_cookie_auth_allows_special_password_characters(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = create_test_client(monkeypatch, tmp_path, admin_password="secret value")
    client.cookies.set(ADMIN_COOKIE_NAME, "secret%20value")

    try:
        response = client.get("/api/assets")
    finally:
        client.close()
        get_settings.cache_clear()

    assert response.status_code == 200
    assert response.json() == []


def test_missing_or_wrong_credentials_are_rejected(authed_client: TestClient) -> None:
    missing_response = authed_client.get("/api/assets")
    authed_client.cookies.set(ADMIN_COOKIE_NAME, "wrong")

    wrong_cookie_response = authed_client.get("/api/assets")
    wrong_header_response = authed_client.get(
        "/api/assets",
        headers={"X-STT-Admin-Password": "wrong"},
    )

    assert missing_response.status_code == 401
    assert wrong_cookie_response.status_code == 401
    assert wrong_header_response.status_code == 401


def test_cookie_auth_does_not_allow_mutating_requests(authed_client: TestClient) -> None:
    authed_client.cookies.set(ADMIN_COOKIE_NAME, "secret")

    response = authed_client.post("/api/speakers/recompute")

    assert response.status_code == 401
