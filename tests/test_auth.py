from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from stt_vault.app import create_app
from stt_vault.settings import get_settings

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
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    test_client = create_test_client(monkeypatch, tmp_path)
    try:
        yield test_client
    finally:
        test_client.close()
        get_settings.cache_clear()


def issue_token(client: TestClient) -> str:
    response = client.post("/api/auth/token", json={"password": "secret"})
    assert response.status_code == 200
    return response.json()["access_token"]


def bearer_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_login_issues_signed_administrator_access_token(client: TestClient) -> None:
    response = client.post("/api/auth/token", json={"password": "secret"})

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


def test_bearer_auth_allows_protected_requests(client: TestClient) -> None:
    response = client.get("/api/assets", headers=bearer_headers(issue_token(client)))

    assert response.status_code == 200
    assert response.json() == []


def test_password_header_and_cookie_cannot_authorize_api_requests(client: TestClient) -> None:
    header_response = client.get(
        "/api/assets",
        headers={"X-STT-Admin-Password": "secret"},
    )
    client.cookies.set("stt-vault-admin-password", "secret")
    cookie_response = client.get("/api/assets")

    assert header_response.status_code == 401
    assert cookie_response.status_code == 401
    assert header_response.json() == {"detail": "Missing bearer token"}
    assert cookie_response.json() == {"detail": "Missing bearer token"}


@pytest.mark.parametrize(
    "claims",
    [
        {"role": "admin", "iss": JWT_ISSUER, "aud": JWT_AUDIENCE},
        {
            "role": "admin",
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
            "iat": datetime.now(UTC) - timedelta(minutes=2),
            "exp": datetime.now(UTC) - timedelta(minutes=1),
            "sub": "single-user-admin",
        },
        {
            "role": "admin",
            "iss": "other-issuer",
            "aud": JWT_AUDIENCE,
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(minutes=1),
            "sub": "single-user-admin",
        },
        {
            "role": "admin",
            "iss": JWT_ISSUER,
            "aud": "other-audience",
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(minutes=1),
            "sub": "single-user-admin",
        },
    ],
)
def test_incomplete_expired_or_wrong_issuer_tokens_are_rejected(
    client: TestClient,
    claims: dict[str, object],
) -> None:
    token = jwt.encode(claims, JWT_SECRET, algorithm="HS256")

    response = client.get("/api/assets", headers=bearer_headers(token))

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid bearer token"}


def test_non_administrator_token_is_forbidden(client: TestClient) -> None:
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": "single-user-reader",
            "role": "reader",
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
            "iat": now,
            "exp": now + timedelta(minutes=1),
        },
        JWT_SECRET,
        algorithm="HS256",
    )

    response = client.get("/api/assets", headers=bearer_headers(token))

    assert response.status_code == 403
    assert response.json() == {"detail": "Administrator token required"}


def test_login_rejects_wrong_password(client: TestClient) -> None:
    response = client.post("/api/auth/token", json={"password": "wrong"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid login credentials"}


def test_login_requires_configured_jwt_secret(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("STT_DATA_DIR", str(data_dir))
    monkeypatch.setenv("STT_DB_PATH", str(data_dir / "app.sqlite3"))
    monkeypatch.setenv("ADMIN_PASSWORD", "secret")
    monkeypatch.setenv("JWT_SECRET", "")
    get_settings.cache_clear()
    client = TestClient(create_app())
    try:
        response = client.post("/api/auth/token", json={"password": "secret"})
    finally:
        client.close()
        get_settings.cache_clear()

    assert response.status_code == 503
    assert response.json() == {"detail": "JWT_SECRET is not configured"}
