import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from _support.upload_routes import auth_headers
from fastapi import FastAPI
from fastapi.testclient import TestClient

from stt_vault.core.auth import require_admin
from stt_vault.core.config import get_settings
from stt_vault.core.models.api import UploadCompletionResponse
from stt_vault.core.models.records import UploadResponse
from stt_vault.persistence import db
from stt_vault.routes.uploads import routes as upload_routes
from stt_vault.routes.uploads.routes import UploadLockRegistry, register_upload_routes
from stt_vault.services.upload_sessions import UploadSessionDependencies, UploadSessionService


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


def test_upload_session_completion_returns_named_response(tmp_path: Path) -> None:
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

    dependencies = UploadSessionDependencies(
        create_upload_session=lambda *_args: upload,
        get_upload_session=lambda *_args: upload,
        update_upload_offset=lambda *_args: None,
        complete_upload_session=lambda *_args: None,
        move_upload=lambda *_args: ("asset-1", stored_path, "audio"),
    )

    completion = UploadSessionService(settings, dependencies).complete("upload-1")

    assert isinstance(completion, UploadCompletionResponse)
    assert completion.model_dump() == {"id": "asset-1", "status": "queued"}


def test_upload_routes_use_injected_session_service() -> None:
    created: list[tuple[str, int]] = []

    class FakeUploadSessions:
        def create(self, filename: str, total_size: int) -> UploadResponse:
            created.append((filename, total_size))
            return {"id": "upload-1", "filename": filename, "size": total_size, "offset": 0}

    app = FastAPI()
    app.dependency_overrides[require_admin] = lambda: None
    register_upload_routes(
        app,
        SimpleNamespace(max_upload_bytes=10),
        FakeUploadSessions(),
    )

    with TestClient(app) as test_client:
        response = test_client.post("/api/uploads", json={"filename": "clip.wav", "size": 4})

    assert response.status_code == 200
    assert response.json() == {"id": "upload-1", "filename": "clip.wav", "size": 4, "offset": 0}
    assert created == [("clip.wav", 4)]


def test_upload_lock_registry_removes_idle_upload_locks() -> None:
    registry = UploadLockRegistry()

    async def acquire_lock() -> None:
        async with registry.acquire("upload-1"):
            assert len(registry._locks) == 1

    asyncio.run(acquire_lock())

    assert registry._locks == {}


def test_upload_lock_registry_keeps_a_lock_until_waiters_finish() -> None:
    registry = UploadLockRegistry()

    async def acquire_locks() -> None:
        first_started = asyncio.Event()
        release_first = asyncio.Event()

        async def first_request() -> None:
            async with registry.acquire("upload-1"):
                first_started.set()
                await release_first.wait()

        async def second_request() -> None:
            await first_started.wait()
            async with registry.acquire("upload-1"):
                return

        first_task = asyncio.create_task(first_request())
        await first_started.wait()
        second_task = asyncio.create_task(second_request())
        await asyncio.sleep(0)
        assert len(registry._locks) == 1
        release_first.set()
        await first_task
        await second_task

    asyncio.run(acquire_locks())

    assert registry._locks == {}


def test_upload_routes_create_lock_registries_isolated_between_application_event_loops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registries: list[UploadLockRegistry] = []

    class TrackingRegistry(UploadLockRegistry):
        def __init__(self) -> None:
            super().__init__()
            registries.append(self)

    monkeypatch.setattr(upload_routes, "UploadLockRegistry", TrackingRegistry)
    settings = SimpleNamespace(max_upload_bytes=10)
    sessions = SimpleNamespace()

    for _ in range(2):
        register_upload_routes(FastAPI(), settings, sessions)

    assert len(registries) == 2
    assert registries[0] is not registries[1]

    async def acquire_lock(registry: UploadLockRegistry) -> None:
        async with registry.acquire("upload-1"):
            assert len(registry._locks) == 1

    asyncio.run(acquire_lock(registries[0]))
    asyncio.run(acquire_lock(registries[1]))
