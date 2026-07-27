from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stt_vault.core.app import create_app
from stt_vault.core.settings import get_settings
from stt_vault.persistence import db
from stt_vault.persistence.db_folders import FolderDataIntegrityError


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
    with TestClient(create_app(), raise_server_exceptions=False) as client:
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
    assert set(root) == {"id", "name", "parent_id", "created_at", "updated_at"}

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
    assert set(tree) == {"folders", "assets"}
    assert tree["assets"] == []
    [tree_root] = tree["folders"]
    [tree_child] = tree_root["children"]
    assert set(tree_root) == {
        "id",
        "name",
        "parent_id",
        "created_at",
        "updated_at",
        "children",
        "assets",
    }
    assert [asset["id"] for asset in tree_child["assets"]] == ["asset-1"]
    [asset] = tree_child["assets"]
    assert set(asset) == {
        "id",
        "filename",
        "title",
        "recorded_at",
        "media_type",
        "duration",
        "status",
        "error",
        "summary_status",
        "parent_folder_id",
        "created_at",
        "updated_at",
    }


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


def test_folder_rename_and_empty_delete(
    folder_client: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = folder_client
    folder = client.post("/api/folders", headers=headers, json={"name": "Draft"}).json()

    rename_response = client.put(
        f"/api/folders/{folder['id']}",
        headers=headers,
        json={"name": "Published"},
    )
    move_response = client.post(
        f"/api/folders/{folder['id']}/move",
        headers=headers,
        json={"parent_id": None},
    )
    delete_response = client.delete(f"/api/folders/{folder['id']}", headers=headers)

    assert move_response.status_code == 200
    assert set(move_response.json()) == {"id", "name", "parent_id", "created_at", "updated_at"}
    assert rename_response.status_code == 200
    assert set(rename_response.json()) == {"id", "name", "parent_id", "created_at", "updated_at"}
    assert rename_response.json()["name"] == "Published"
    assert delete_response.status_code == 200
    assert delete_response.json() == {"status": "deleted"}


def test_folder_delete_rejects_non_empty_folder(
    folder_client: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = folder_client
    folder = client.post("/api/folders", headers=headers, json={"name": "Meetings"}).json()
    settings = get_settings()
    db.create_asset(
        settings.stt_db_path,
        "asset-1",
        "meeting.mp4",
        "video",
        settings.media_dir / "asset-1" / "meeting.mp4",
        parent_folder_id=folder["id"],
    )

    response = client.delete(f"/api/folders/{folder['id']}", headers=headers)

    assert response.status_code == 409
    assert response.json() == {"detail": "Folder is not empty"}


def test_folder_routes_report_missing_records(
    folder_client: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = folder_client

    create_response = client.post(
        "/api/folders",
        headers=headers,
        json={"name": "Child", "parent_id": "missing"},
    )
    move_response = client.post(
        "/api/folders/missing/move",
        headers=headers,
        json={"parent_id": None},
    )
    rename_response = client.put(
        "/api/folders/missing",
        headers=headers,
        json={"name": "Renamed"},
    )

    assert create_response.json() == {"detail": "Parent folder not found"}
    assert create_response.status_code == 404
    assert move_response.json() == {"detail": "Folder not found"}
    assert move_response.status_code == 404
    assert rename_response.json() == {"detail": "Folder not found"}
    assert rename_response.status_code == 404


def test_folder_persistence_rejects_malformed_rows(
    folder_client: tuple[TestClient, dict[str, str]],
) -> None:
    _, _headers = folder_client
    settings = get_settings()
    folder = db.create_folder(settings.stt_db_path, "Malformed")
    with db.transaction(settings.stt_db_path) as conn:
        conn.execute("UPDATE folders SET created_at = ? WHERE id = ?", ("invalid", folder.id))

    assert db.get_folder(settings.stt_db_path, "missing") is None
    with pytest.raises(FolderDataIntegrityError):
        db.list_folders(settings.stt_db_path)


def test_folder_tree_rejects_unknown_folder_parent(tmp_path: Path) -> None:
    db_path = tmp_path / "app.sqlite3"
    db.initialize(db_path)
    folder = db.create_folder(db_path, "Orphan")
    with db.transaction(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("UPDATE folders SET parent_id = 'missing' WHERE id = ?", (folder.id,))

    with pytest.raises(FolderDataIntegrityError, match="unknown parent"):
        db.list_folder_tree(db_path)


def test_folder_tree_rejects_unknown_asset_parent(tmp_path: Path) -> None:
    db_path = tmp_path / "app.sqlite3"
    db.initialize(db_path)
    db.create_asset(db_path, "asset-1", "meeting.wav", "audio", tmp_path / "meeting.wav")
    with db.transaction(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("UPDATE assets SET parent_folder_id = 'missing' WHERE id = 'asset-1'")

    with pytest.raises(FolderDataIntegrityError, match="unknown folder"):
        db.list_folder_tree(db_path)


def test_folder_tree_rejects_malformed_asset_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "app.sqlite3"
    db.initialize(db_path)
    db.create_asset(db_path, "asset-1", "meeting.wav", "audio", tmp_path / "meeting.wav")
    with db.transaction(db_path) as conn:
        conn.execute("UPDATE assets SET created_at = 'invalid' WHERE id = 'asset-1'")

    with pytest.raises(FolderDataIntegrityError, match="Folder asset record is invalid"):
        db.list_folder_tree(db_path)


def test_folder_tree_rejects_cycles_and_unreachable_folders(tmp_path: Path) -> None:
    db_path = tmp_path / "app.sqlite3"
    db.initialize(db_path)
    root = db.create_folder(db_path, "Root")
    child = db.create_folder(db_path, "Child", parent_id=root.id)
    with db.transaction(db_path) as conn:
        conn.execute("UPDATE folders SET parent_id = ? WHERE id = ?", (child.id, root.id))

    with pytest.raises(FolderDataIntegrityError, match="cycle or unreachable"):
        db.list_folder_tree(db_path)


@pytest.mark.parametrize(
    ("method", "path_template", "payload"),
    [
        ("GET", "/api/folders", None),
        ("POST", "/api/folders/{folder_id}/move", {"parent_id": None}),
        ("PUT", "/api/folders/{folder_id}", {"name": "Renamed"}),
    ],
)
def test_folder_routes_report_malformed_persisted_rows_as_server_errors(
    folder_client: tuple[TestClient, dict[str, str]],
    method: str,
    path_template: str,
    payload: dict[str, str | None] | None,
) -> None:
    client, headers = folder_client
    folder = db.create_folder(get_settings().stt_db_path, "Malformed")
    with db.transaction(get_settings().stt_db_path) as conn:
        conn.execute("UPDATE folders SET created_at = ? WHERE id = ?", ("invalid", folder.id))

    response = client.request(
        method,
        path_template.format(folder_id=folder.id),
        headers=headers,
        json=payload,
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Folder data is invalid"}


def test_folder_routes_preserve_invalid_name_validation(
    folder_client: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = folder_client
    folder = client.post("/api/folders", headers=headers, json={"name": "Draft"}).json()

    create_response = client.post("/api/folders", headers=headers, json={"name": "  "})
    rename_response = client.put(
        f"/api/folders/{folder['id']}",
        headers=headers,
        json={"name": "folder/name"},
    )

    assert create_response.status_code == 422
    assert create_response.json() == {"detail": "Folder name is required"}
    assert rename_response.status_code == 422
    assert rename_response.json() == {"detail": "Folder name cannot contain a path separator"}


def test_folder_tree_route_rejects_malformed_asset_rows(
    folder_client: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = folder_client
    settings = get_settings()
    db.create_asset(
        settings.stt_db_path,
        "asset-1",
        "meeting.wav",
        "audio",
        settings.media_dir / "asset-1" / "meeting.wav",
    )
    with db.transaction(settings.stt_db_path) as conn:
        conn.execute("UPDATE assets SET created_at = 'invalid' WHERE id = 'asset-1'")

    response = client.get("/api/folders", headers=headers)

    assert response.status_code == 500
    assert response.json() == {"detail": "Folder data is invalid"}


def test_folder_tree_rejects_invalid_asset_json(
    folder_client: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = folder_client
    settings = get_settings()
    db.create_asset(
        settings.stt_db_path,
        "asset-1",
        "meeting.wav",
        "audio",
        settings.media_dir / "asset-1" / "meeting.wav",
    )
    with db.transaction(settings.stt_db_path) as conn:
        conn.execute("UPDATE assets SET error = 'not-json' WHERE id = 'asset-1'")

    with pytest.raises(FolderDataIntegrityError):
        db.list_folder_tree(settings.stt_db_path)

    response = client.get("/api/folders", headers=headers)

    assert response.status_code == 500
    assert response.json() == {"detail": "Folder data is invalid"}


def test_folder_tree_route_rejects_orphan_folder_and_asset_parents(
    folder_client: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = folder_client
    settings = get_settings()
    folder = db.create_folder(settings.stt_db_path, "Orphan")
    db.create_asset(
        settings.stt_db_path,
        "asset-1",
        "meeting.wav",
        "audio",
        settings.media_dir / "asset-1" / "meeting.wav",
    )
    with db.transaction(settings.stt_db_path) as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("UPDATE folders SET parent_id = 'missing' WHERE id = ?", (folder.id,))

    folder_response = client.get("/api/folders", headers=headers)

    assert folder_response.status_code == 500
    assert folder_response.json() == {"detail": "Folder data is invalid"}

    with db.transaction(settings.stt_db_path) as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("UPDATE folders SET parent_id = NULL WHERE id = ?", (folder.id,))
        conn.execute("UPDATE assets SET parent_folder_id = 'missing' WHERE id = 'asset-1'")

    asset_response = client.get("/api/folders", headers=headers)

    assert asset_response.status_code == 500
    assert asset_response.json() == {"detail": "Folder data is invalid"}


def test_folder_tree_route_rejects_cycles(
    folder_client: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = folder_client
    settings = get_settings()
    root = db.create_folder(settings.stt_db_path, "Root")
    child = db.create_folder(settings.stt_db_path, "Child", parent_id=root.id)
    with db.transaction(settings.stt_db_path) as conn:
        conn.execute("UPDATE folders SET parent_id = ? WHERE id = ?", (child.id, root.id))

    response = client.get("/api/folders", headers=headers)

    assert response.status_code == 500
    assert response.json() == {"detail": "Folder data is invalid"}
