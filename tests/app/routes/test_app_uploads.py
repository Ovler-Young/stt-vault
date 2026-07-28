from pathlib import Path
from typing import NoReturn

import pytest
from _support.upload_routes import api_routes, auth_headers
from fastapi import HTTPException
from fastapi.testclient import TestClient

from stt_vault.core.config import get_settings
from stt_vault.core.models.api import AssetResponse, UploadCompletionResponse
from stt_vault.persistence import db


def test_upload_completion_route_declares_named_response_model(client: TestClient) -> None:
    completion_route = next(
        route
        for route in api_routes(client.app)
        if route.path == "/api/uploads/{upload_id}/complete" and "POST" in (route.methods or set())
    )

    assert completion_route.response_model is UploadCompletionResponse


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
        files={"file": ("clip.wav", b"audio", "audio/wav")},
    )

    assert response.status_code == 200
    asset_id = response.json()["id"]
    asset = db.get_asset(get_settings().stt_db_path, asset_id)
    assert asset is not None
    assert asset["filename"] == "clip.wav"
    assert Path(asset["original_path"]).read_bytes() == b"audio"
    list_response = client.get("/api/assets", headers=auth_headers(client))
    detail_response = client.get(f"/api/assets/{asset_id}", headers=auth_headers(client))
    assert list_response.status_code == 200
    assert detail_response.status_code == 200
    assert AssetResponse.model_validate(list_response.json()[0]).id == asset_id
    assert AssetResponse.model_validate(detail_response.json()).id == asset_id


def test_single_upload_preserves_storage_http_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reject_upload(_media_dir: Path, _filename: str, _source_path: Path) -> NoReturn:
        raise HTTPException(status_code=413, detail="Upload is too large")

    monkeypatch.setattr("stt_vault.routes.assets.collection.store_upload", reject_upload)

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
    def fail_upload(_media_dir: Path, _filename: str, _source_path: Path) -> NoReturn:
        raise OSError("storage unavailable")

    monkeypatch.setattr("stt_vault.routes.assets.collection.store_upload", fail_upload)

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
