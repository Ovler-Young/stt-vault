import json
import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import NoReturn

import pytest
from fastapi.testclient import TestClient

from stt_vault.core.app import create_app
from stt_vault.core.config import get_settings
from stt_vault.core.diagnostics.logging import StructuredFormatter
from stt_vault.core.models.persistence_errors import FolderDataIntegrityError
from stt_vault.core.models.records import FolderCreate, NewAsset
from stt_vault.persistence.sqlite_database import SqliteDatabase


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


def test_folder_persistence_rejects_malformed_rows(
    folder_client: tuple[TestClient, dict[str, str]],
) -> None:
    _, _headers = folder_client
    settings = get_settings()
    database = SqliteDatabase(settings.stt_db_path)
    folder = database.create_folder(FolderCreate("Malformed"))
    with database._transaction() as conn:
        conn.execute("UPDATE folders SET created_at = ? WHERE id = ?", ("invalid", folder.id))

    assert database.get_folder("missing") is None
    with pytest.raises(FolderDataIntegrityError):
        database.list_folders()


def test_folder_tree_rejects_unknown_folder_parent(tmp_path: Path) -> None:
    database = SqliteDatabase(tmp_path / "app.sqlite3")
    database.initialize()
    folder = database.create_folder(FolderCreate("Orphan"))
    with sqlite3.connect(database._db_path) as conn:
        conn.execute("UPDATE folders SET parent_id = 'missing' WHERE id = ?", (folder.id,))

    with pytest.raises(FolderDataIntegrityError, match="unknown parent"):
        database.list_folder_tree()


def test_folder_tree_rejects_unknown_asset_parent(tmp_path: Path) -> None:
    database = SqliteDatabase(tmp_path / "app.sqlite3")
    database.initialize()
    database.create_asset(NewAsset("asset-1", "meeting.wav", "audio", tmp_path / "meeting.wav"))
    with sqlite3.connect(database._db_path) as conn:
        conn.execute("UPDATE assets SET parent_folder_id = 'missing' WHERE id = 'asset-1'")

    with pytest.raises(FolderDataIntegrityError, match="unknown folder"):
        database.list_folder_tree()


def test_folder_tree_rejects_malformed_asset_rows(tmp_path: Path) -> None:
    database = SqliteDatabase(tmp_path / "app.sqlite3")
    database.initialize()
    database.create_asset(NewAsset("asset-1", "meeting.wav", "audio", tmp_path / "meeting.wav"))
    with database._transaction() as conn:
        conn.execute("UPDATE assets SET created_at = 'invalid' WHERE id = 'asset-1'")

    with pytest.raises(FolderDataIntegrityError, match="Folder asset record is invalid"):
        database.list_folder_tree()


def test_folder_tree_rejects_cycles_and_unreachable_folders(tmp_path: Path) -> None:
    database = SqliteDatabase(tmp_path / "app.sqlite3")
    database.initialize()
    root = database.create_folder(FolderCreate("Root"))
    child = database.create_folder(FolderCreate("Child", root.id))
    with database._transaction() as conn:
        conn.execute("UPDATE folders SET parent_id = ? WHERE id = ?", (child.id, root.id))

    with pytest.raises(FolderDataIntegrityError, match="cycle or unreachable"):
        database.list_folder_tree()


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
    database = SqliteDatabase(get_settings().stt_db_path)
    folder = database.create_folder(FolderCreate("Malformed"))
    with database._transaction() as conn:
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
    database = SqliteDatabase(settings.stt_db_path)
    database.create_asset(
        NewAsset("asset-1", "meeting.wav", "audio", settings.media_dir / "asset-1" / "meeting.wav")
    )
    with database._transaction() as conn:
        conn.execute("UPDATE assets SET created_at = 'invalid' WHERE id = 'asset-1'")

    response = client.get("/api/folders", headers=headers)

    assert response.status_code == 500
    assert response.json() == {"detail": "Folder data is invalid"}


def test_folder_tree_rejects_invalid_asset_json(
    folder_client: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = folder_client
    settings = get_settings()
    database = SqliteDatabase(settings.stt_db_path)
    database.create_asset(
        NewAsset("asset-1", "meeting.wav", "audio", settings.media_dir / "asset-1" / "meeting.wav")
    )
    with database._transaction() as conn:
        conn.execute("UPDATE assets SET error = 'not-json' WHERE id = 'asset-1'")

    with pytest.raises(FolderDataIntegrityError):
        database.list_folder_tree()

    response = client.get("/api/folders", headers=headers)

    assert response.status_code == 500
    assert response.json() == {"detail": "Folder data is invalid"}


def test_folder_tree_route_rejects_orphan_folder_and_asset_parents(
    folder_client: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = folder_client
    settings = get_settings()
    database = SqliteDatabase(settings.stt_db_path)
    folder = database.create_folder(FolderCreate("Orphan"))
    database.create_asset(
        NewAsset("asset-1", "meeting.wav", "audio", settings.media_dir / "asset-1" / "meeting.wav")
    )
    with sqlite3.connect(database._db_path) as conn:
        conn.execute("UPDATE folders SET parent_id = 'missing' WHERE id = ?", (folder.id,))

    folder_response = client.get("/api/folders", headers=headers)

    assert folder_response.status_code == 500
    assert folder_response.json() == {"detail": "Folder data is invalid"}

    with sqlite3.connect(database._db_path) as conn:
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
    database = SqliteDatabase(settings.stt_db_path)
    root = database.create_folder(FolderCreate("Root"))
    child = database.create_folder(FolderCreate("Child", root.id))
    with database._transaction() as conn:
        conn.execute("UPDATE folders SET parent_id = ? WHERE id = ?", (child.id, root.id))

    response = client.get("/api/folders", headers=headers)

    assert response.status_code == 500
    assert response.json() == {"detail": "Folder data is invalid"}


@pytest.mark.parametrize(
    ("method", "path", "payload", "db_function", "operation"),
    [
        ("GET", "/api/folders", None, "list_folder_tree", "list"),
        ("POST", "/api/folders", {"name": "Folder"}, "create_folder", "add"),
        (
            "POST",
            "/api/folders/folder-1/move",
            {"parent_id": None},
            "move_folder",
            "move",
        ),
        ("PUT", "/api/folders/folder-1", {"name": "Folder"}, "rename_folder", "rename"),
        ("DELETE", "/api/folders/folder-1", None, "delete_folder", "remove"),
    ],
)
def test_folder_integrity_errors_log_redacted_operation_diagnostics(
    folder_client: tuple[TestClient, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    method: str,
    path: str,
    payload: dict[str, str | None] | None,
    db_function: str,
    operation: str,
) -> None:
    client, headers = folder_client

    def raise_integrity_error(*_args: object, **_kwargs: object) -> NoReturn:
        raise FolderDataIntegrityError("password=private-value at /srv/private/folders.sqlite3")

    monkeypatch.setattr(SqliteDatabase, db_function, raise_integrity_error)

    with caplog.at_level(logging.ERROR, logger="stt_vault.routes.folders.routes"):
        response = client.request(method, path, headers=headers, json=payload)

    assert response.status_code == 500
    assert response.json() == {"detail": "Folder data is invalid"}
    event = json.loads(StructuredFormatter().format(caplog.records[-1]))
    assert event["event_name"] == "folders.data_integrity_error"
    assert event["operation"] == operation
    assert event["cause"] == "[redacted] at [path]"
    assert "private-value" not in json.dumps(event)
