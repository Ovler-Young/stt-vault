from pathlib import Path

import pytest
from _support.upload_routes import api_routes, auth_headers
from fastapi.testclient import TestClient

from stt_vault.core.config import get_settings
from stt_vault.core.models.api import (
    AssetBatchUploadResponse,
    AssetResponse,
    AssetUploadResponse,
    UploadCompletionResponse,
    UploadProgressResponse,
)
from stt_vault.persistence.sqlite_database import SqliteDatabase
from stt_vault.services.asset_uploads import AssetUploadPersistenceError, AssetUploadTooLargeError


@pytest.fixture(autouse=True)
def mock_media_type(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "stt_vault.services.media_storage.ffprobe_media_type", lambda _path: "audio"
    )


def test_upload_completion_route_declares_named_response_model(client: TestClient) -> None:
    completion_route = next(
        route
        for route in api_routes(client.app)
        if route.path == "/api/uploads/{upload_id}/complete" and "POST" in (route.methods or set())
    )

    assert completion_route.response_model is UploadCompletionResponse


def test_upload_progress_routes_declare_named_response_model(client: TestClient) -> None:
    routes = api_routes(client.app)
    progress_routes = [
        route for route in routes if route.path in {"/api/uploads", "/api/uploads/{upload_id}"}
    ]

    assert {route.response_model for route in progress_routes} == {UploadProgressResponse}


def test_asset_upload_routes_declare_named_response_models(client: TestClient) -> None:
    routes = api_routes(client.app)
    single_route = next(
        route
        for route in routes
        if route.path == "/api/assets" and "POST" in (route.methods or set())
    )
    batch_route = next(
        route
        for route in routes
        if route.path == "/api/assets/batch" and "POST" in (route.methods or set())
    )

    assert single_route.response_model is AssetUploadResponse
    assert batch_route.response_model is AssetBatchUploadResponse


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


def test_single_upload_uses_shared_persistence_sequence(client: TestClient) -> None:
    response = client.post(
        "/api/assets",
        headers=auth_headers(client),
        files={"file": ("clip.uncommon", b"audio", "audio/wav")},
    )

    assert response.status_code == 200
    asset_id = response.json()["id"]
    asset = SqliteDatabase(get_settings().stt_db_path).get_asset(asset_id)
    assert asset is not None
    assert asset.filename == "clip.uncommon"
    assert asset.media_type == "audio"
    assert Path(asset.original_path).read_bytes() == b"audio"
    list_response = client.get("/api/assets", headers=auth_headers(client))
    detail_response = client.get(f"/api/assets/{asset_id}", headers=auth_headers(client))
    assert list_response.status_code == 200
    assert detail_response.status_code == 200
    assert AssetResponse.model_validate(list_response.json()[0]).id == asset_id
    assert AssetResponse.model_validate(detail_response.json()).id == asset_id


def test_single_upload_preserves_storage_http_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reject_upload(*_args: object, **_kwargs: object) -> None:
        raise AssetUploadTooLargeError("Upload is too large")

    monkeypatch.setattr("stt_vault.routes.assets.collection.store_asset_upload", reject_upload)

    response = client.post(
        "/api/assets",
        headers=auth_headers(client),
        files={"file": ("clip.wav", b"audio", "audio/wav")},
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "Upload is too large"}


def test_single_upload_maps_storage_failure_to_generic_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_upload(*_args: object, **_kwargs: object) -> None:
        raise AssetUploadPersistenceError("Upload could not be stored")

    monkeypatch.setattr("stt_vault.routes.assets.collection.store_asset_upload", fail_upload)

    response = client.post(
        "/api/assets",
        headers=auth_headers(client),
        files={"file": ("clip.wav", b"audio", "audio/wav")},
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Upload could not be stored"}


def test_audio_probe_error_does_not_disclose_paths_or_credentials(
    client: TestClient, monkeypatch
) -> None:
    response = client.post(
        "/api/assets",
        headers=auth_headers(client),
        files={"file": ("clip.wav", b"audio", "audio/wav")},
    )
    asset_id = response.json()["id"]

    def fail_probe(_path: Path) -> list[object]:
        raise RuntimeError("/private/clip.wav token=secret")

    monkeypatch.setattr("stt_vault.routes.assets.media.ffprobe_audio_streams", fail_probe)
    probe_response = client.get(
        f"/api/assets/{asset_id}/audio-tracks", headers=auth_headers(client)
    )

    assert probe_response.status_code == 400
    assert probe_response.json() == {"detail": "Could not probe audio tracks"}
