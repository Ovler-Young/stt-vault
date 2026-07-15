from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from stt_vault.app import create_app
from stt_vault.settings import get_settings

ADMIN_COOKIE_NAME = "stt-vault-admin-password"
JWT_SECRET = "test-jwt-secret-that-is-long-enough-for-hs256-signing"
JWT_ISSUER = "stt-vault-test"
JWT_AUDIENCE = "stt-vault-test-api"


def create_test_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    admin_password: str = "secret",
) -> TestClient:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("STT_DATA_DIR", str(data_dir))
    monkeypatch.setenv("STT_DB_PATH", str(data_dir / "app.sqlite3"))
    monkeypatch.setenv("ADMIN_PASSWORD", admin_password)
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("JWT_ISSUER", JWT_ISSUER)
    monkeypatch.setenv("JWT_AUDIENCE", JWT_AUDIENCE)
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


def issue_token(client: TestClient) -> str:
    response = client.post("/api/auth/token", json={"password": "secret"})
    assert response.status_code == 200
    return response.json()["access_token"]


def bearer_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_login_issues_signed_administrator_access_token(authed_client: TestClient) -> None:
    response = authed_client.post("/api/auth/token", json={"password": "secret"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["token_type"] == "bearer"
    assert payload["expires_in"] == 3600
    claims = jwt.decode(
        payload["access_token"],
        JWT_SECRET,
        algorithms=["HS256"],
        issuer=JWT_ISSUER,
        audience=JWT_AUDIENCE,
    )
    assert claims["sub"] == "single-user-admin"
    assert claims["role"] == "admin"


def test_bearer_auth_allows_api_requests(authed_client: TestClient) -> None:
    response = authed_client.get("/api/assets", headers=bearer_headers(issue_token(authed_client)))

    assert response.status_code == 200
    assert response.json() == []


def test_password_header_and_cookie_cannot_authorize_api_requests(
    authed_client: TestClient,
) -> None:
    header_response = authed_client.get(
        "/api/assets",
        headers={"X-STT-Admin-Password": "secret"},
    )
    authed_client.cookies.set(ADMIN_COOKIE_NAME, "secret")
    cookie_response = authed_client.get("/api/assets")

    assert header_response.status_code == 401
    assert cookie_response.status_code == 401


def test_missing_or_wrong_credentials_are_rejected(authed_client: TestClient) -> None:
    missing_response = authed_client.get("/api/assets")
    wrong_token = jwt.encode(
        {
            "sub": "single-user-admin",
            "role": "admin",
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(minutes=1),
        },
        "wrong-jwt-secret-that-is-long-enough-for-hs256-signing",
        algorithm="HS256",
    )
    wrong_token_response = authed_client.get("/api/assets", headers=bearer_headers(wrong_token))

    assert missing_response.status_code == 401
    assert wrong_token_response.status_code == 401


def test_bearer_token_is_required_for_mutating_requests(authed_client: TestClient) -> None:
    response = authed_client.post(
        "/api/speakers/recompute",
        headers=bearer_headers(issue_token(authed_client)),
    )

    assert response.status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/api/assets/missing/media",
        "/api/assets/missing/exports/vtt",
        "/api/assets/missing/visual-events/0/thumbnail",
    ],
)
def test_resource_query_token_allows_read_only_asset_requests(
    authed_client: TestClient,
    path: str,
) -> None:
    token = issue_token(authed_client)
    response = authed_client.get(path, params={"access_token": token})

    assert response.status_code == 404
