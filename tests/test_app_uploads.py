from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import NoReturn

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from stt_vault.core.api_models import AssetResponse, UploadCompletionResponse
from stt_vault.core.app import create_app
from stt_vault.core.settings import get_settings
from stt_vault.persistence import db
from stt_vault.services.upload_sessions import UploadSessionService

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


def api_routes(app) -> list[APIRoute]:
    api_routes = []
    routes = list(app.routes)
    for route in routes:
        original_router = getattr(route, "original_router", None)
        if original_router is not None:
            routes.extend(original_router.routes)
            continue
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/"):
            continue
        api_routes.append(route)
    return api_routes


def api_route_pairs(app) -> list[tuple[str, str]]:
    pairs = []
    for route in api_routes(app):
        for method in sorted(route.methods or []):
            if method != "HEAD":
                pairs.append((method, route.path))
    return pairs


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

    monkeypatch.setattr("stt_vault.routes.asset_collection.store_upload", reject_upload)

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

    monkeypatch.setattr("stt_vault.routes.asset_collection.store_upload", fail_upload)

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

    monkeypatch.setattr("stt_vault.routes.asset_media.ffprobe_audio_streams", fail_probe)
    probe_response = client.get(
        f"/api/assets/{asset_id}/audio-tracks", headers=auth_headers(client)
    )

    assert probe_response.status_code == 400
    assert probe_response.json() == {"detail": "Could not probe audio tracks"}


def test_ranged_upload_tracks_offset_and_completes_asset(client: TestClient) -> None:
    headers = auth_headers(client)
    create_response = client.post(
        "/api/uploads",
        headers=headers,
        json={"filename": "2026-07-15_12-57-52.mp4", "size": 10},
    )

    assert create_response.status_code == 200
    upload_id = create_response.json()["id"]
    upload = db.get_upload_session(get_settings().stt_db_path, upload_id)
    assert upload is not None
    Path(upload["temp_path"]).write_bytes(b"unconfirmed")
    first_response = client.put(
        f"/api/uploads/{upload_id}",
        headers={**headers, "Content-Range": "bytes 0-4/10"},
        content=b"first",
    )
    rejected_response = client.put(
        f"/api/uploads/{upload_id}",
        headers={**headers, "Content-Range": "bytes 7-9/10"},
        content=b"bad",
    )
    short_response = client.put(
        f"/api/uploads/{upload_id}",
        headers={**headers, "Content-Range": "bytes 5-9/10"},
        content=b"no",
    )
    status_response = client.get(f"/api/uploads/{upload_id}", headers=headers)

    assert first_response.status_code == 200
    assert first_response.json()["offset"] == 5
    assert rejected_response.status_code == 409
    assert rejected_response.json() == {"detail": "Expected range to start at byte 5"}
    assert short_response.status_code == 400
    assert short_response.json() == {"detail": "Content-Range does not match body size"}
    assert status_response.json()["offset"] == 5

    final_response = client.put(
        f"/api/uploads/{upload_id}",
        headers={**headers, "Content-Range": "bytes 5-9/10"},
        content=b"final",
    )
    complete_response = client.post(f"/api/uploads/{upload_id}/complete", headers=headers)

    assert final_response.status_code == 200
    assert final_response.json()["offset"] == 10
    assert complete_response.status_code == 200
    completion = UploadCompletionResponse.model_validate(complete_response.json())
    assert (
        complete_response.json()
        == completion.model_dump()
        == {
            "id": completion.id,
            "status": "queued",
        }
    )
    asset = db.get_asset(get_settings().stt_db_path, complete_response.json()["id"])
    assert asset is not None
    assert asset["filename"] == "2026-07-15_12-57-52.mp4"
    assert asset["recorded_at"] == 1_784_120_272
    assert Path(asset["original_path"]).read_bytes() == b"firstfinal"
    assert client.get(f"/api/uploads/{upload_id}", headers=headers).status_code == 404


def test_upload_session_completion_returns_named_response(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = SimpleNamespace(
        stt_db_path=tmp_path / "app.sqlite3",
        uploads_dir=tmp_path / "uploads",
        media_dir=tmp_path / "media",
    )
    temp_path = settings.uploads_dir / "upload.part"
    temp_path.parent.mkdir(parents=True)
    temp_path.write_bytes(b"upload")
    upload = {
        "id": "upload-1",
        "filename": "clip.wav",
        "total_size": 6,
        "offset": 6,
        "temp_path": str(temp_path),
    }
    stored_path = settings.media_dir / "asset-1" / "original.wav"

    monkeypatch.setattr(
        "stt_vault.services.upload_sessions.get_upload_session", lambda *_args: upload
    )
    monkeypatch.setattr(
        "stt_vault.services.upload_sessions.move_upload",
        lambda *_args: ("asset-1", stored_path, "audio"),
    )
    monkeypatch.setattr(
        "stt_vault.services.upload_sessions.complete_upload_session", lambda *_args: None
    )

    completion = UploadSessionService(settings).complete("upload-1")

    assert isinstance(completion, UploadCompletionResponse)
    assert completion.model_dump() == {"id": "asset-1", "status": "queued"}


def test_upload_size_limit_uses_one_megabyte_boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    app = create_test_app(monkeypatch, tmp_path)
    test_client = TestClient(app)
    try:
        headers = auth_headers(test_client)
        direct_upload = test_client.post(
            "/api/assets",
            headers=headers,
            files={"file": ("too-large.wav", b"x" * (1024 * 1024 + 1), "audio/wav")},
        )
        exact_limit = test_client.post(
            "/api/uploads",
            headers=headers,
            json={"filename": "limit.wav", "size": 1024 * 1024},
        )
        over_limit = test_client.post(
            "/api/uploads",
            headers=headers,
            json={"filename": "too-large.wav", "size": 1024 * 1024 + 1},
        )
    finally:
        test_client.close()
        get_settings.cache_clear()

    assert direct_upload.status_code == 413
    assert direct_upload.json() == {"detail": "Upload is too large"}
    assert exact_limit.status_code == 200
    assert over_limit.status_code == 413
