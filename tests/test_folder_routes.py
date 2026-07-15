from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stt_vault import db
from stt_vault.app import create_app
from stt_vault.settings import get_settings


@pytest.fixture
def folder_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[tuple[TestClient, dict[str, str]]]:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("STT_DATA_DIR", str(data_dir))
    monkeypatch.setenv("STT_DB_PATH", str(data_dir / "app.sqlite3"))
    monkeypatch.setenv("ADMIN_PASSWORD", "secret")
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-that-is-long-enough-for-hs256-signing")
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        token_response = client.post("/api/auth/token", json={"password": "secret"})
        assert token_response.status_code == 200
        token = token_response.json()["access_token"]
        yield client, {"Authorization": f"Bearer {token}"}
    get_settings.cache_clear()


def test_folder_routes_build_a_tree_and_move_assets(
    folder_client: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = folder_client
    root_response = client.post("/api/folders", headers=headers, json={"name": "Meetings"})
    assert root_response.status_code == 200
    root = root_response.json()

    child_response = client.post(
        "/api/folders",
        headers=headers,
        json={"name": "Planning", "parent_id": root["id"]},
    )
    assert child_response.status_code == 200
    child = child_response.json()

    settings = get_settings()
    db.create_asset(
        settings.stt_db_path,
        "asset-1",
        "roadmap.wav",
        "audio",
        settings.media_dir / "asset-1" / "roadmap.wav",
    )
    move_response = client.post(
        "/api/assets/asset-1/move",
        headers=headers,
        json={"parent_folder_id": child["id"]},
    )

    assert move_response.status_code == 200
    assert move_response.json()["parent_folder_id"] == child["id"]
    tree_response = client.get("/api/folders", headers=headers)
    assert tree_response.status_code == 200
    tree = tree_response.json()
    assert tree["assets"] == []
    [tree_root] = tree["folders"]
    [tree_child] = tree_root["children"]
    assert [asset["id"] for asset in tree_child["assets"]] == ["asset-1"]


def test_folder_move_rejects_descendant_target(
    folder_client: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = folder_client
    root = client.post("/api/folders", headers=headers, json={"name": "Root"}).json()
    child = client.post(
        "/api/folders",
        headers=headers,
        json={"name": "Child", "parent_id": root["id"]},
    ).json()

    response = client.post(
        f"/api/folders/{root['id']}/move",
        headers=headers,
        json={"parent_id": child["id"]},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "A folder cannot be moved into a descendant"}
