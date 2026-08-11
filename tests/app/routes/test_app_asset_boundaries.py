from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from stt_vault.core.app import create_app
from stt_vault.core.config import get_settings
from stt_vault.core.models.api import AssetResponse
from stt_vault.core.models.records import (
    ErrorRecord,
    JobEventCreate,
    NewAsset,
    ReplaceTranscriptTimedUnits,
    TimedTranscriptUnit,
    TranscriptChunkUpsert,
    TranscriptSegment,
)
from stt_vault.persistence.sqlite_database import SqliteDatabase

JWT_SECRET = "test-jwt-secret-that-is-long-enough-for-hs256-signing"


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


def database() -> SqliteDatabase:
    return SqliteDatabase(get_settings().stt_db_path)


def test_asset_response_rejects_malformed_database_rows() -> None:
    with pytest.raises(ValidationError):
        AssetResponse.model_validate({"id": "asset-1", "filename": "clip.wav"})


def test_asset_response_rejects_unknown_database_fields() -> None:
    with pytest.raises(ValidationError):
        AssetResponse.model_validate(
            {
                "id": "asset-1",
                "filename": "clip.wav",
                "media_type": "audio",
                "status": "queued",
                "created_at": 1,
                "updated_at": 1,
                "unexpected": "unvalidated",
            }
        )


def test_asset_api_does_not_expose_persisted_secret_or_path(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "stt_vault.services.media_storage.ffprobe_media_type", lambda _path: "audio"
    )
    upload = client.post(
        "/api/assets",
        headers=auth_headers(client),
        files={"file": ("clip.wav", b"audio", "audio/wav")},
    )
    asset_id = upload.json()["id"]
    database().mark_failed(
        asset_id,
        ErrorRecord("provider", "Bearer api-token /srv/private/clip.wav"),
    )

    response = client.get(f"/api/assets/{asset_id}", headers=auth_headers(client))

    assert response.status_code == 200
    assert "api-token" not in response.text
    assert "/srv/private/clip.wav" not in response.text


def test_asset_events_uses_the_dedicated_event_query(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = get_settings().stt_db_path
    database().create_asset(NewAsset("asset-1", "clip.wav", "audio", db_path.parent / "clip.wav"))
    database().add_event(JobEventCreate("asset-1", "info", "queued", "Job queued"))

    def fail_if_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("event history must not load the asset aggregate")

    monkeypatch.setattr(SqliteDatabase, "get_asset", fail_if_called)

    response = client.get("/api/assets/asset-1/events", headers=auth_headers(client))

    assert response.status_code == 200
    assert [event["message"] for event in response.json()] == ["Job queued"]


def test_asset_detail_defaults_to_event_history_and_supports_lean_reads(client: TestClient) -> None:
    db_path = get_settings().stt_db_path
    database().create_asset(NewAsset("asset-1", "clip.wav", "audio", db_path.parent / "clip.wav"))
    database().add_event(JobEventCreate("asset-1", "info", "queued", "Job queued"))
    headers = auth_headers(client)

    legacy_response = client.get("/api/assets/asset-1", headers=headers)
    lean_response = client.get("/api/assets/asset-1?include_event_history=false", headers=headers)

    assert legacy_response.status_code == 200
    assert [event["message"] for event in legacy_response.json()["event_history"]] == ["Job queued"]
    assert lean_response.status_code == 200
    assert lean_response.json()["event_history"] is None


def test_asset_detail_orders_timed_units_and_keeps_segment_only_chunks_unchanged(
    client: TestClient,
) -> None:
    db_path = get_settings().stt_db_path
    db = database()
    db.create_asset(NewAsset("asset-1", "clip.wav", "audio", db_path.parent / "clip.wav"))
    db.upsert_transcript_chunk(
        TranscriptChunkUpsert("asset-1", 1, TranscriptSegment(1.0, 2.0, "SPEAKER_00", "later"), 1)
    )
    db.upsert_transcript_chunk(
        TranscriptChunkUpsert("asset-1", 0, TranscriptSegment(0.0, 1.0, "SPEAKER_00", "first"), 1)
    )
    db.replace_transcript_timed_units(
        ReplaceTranscriptTimedUnits(
            "asset-1",
            1,
            (
                TimedTranscriptUnit(0, "lat", 1000, 1500, None, "en", "word"),
                TimedTranscriptUnit(1, "er", 1500, 2000, None, "en", "word"),
            ),
        )
    )
    persisted_chunks = db.list_transcript_chunks("asset-1")

    response = client.get("/api/assets/asset-1", headers=auth_headers(client))

    assert response.status_code == 200
    segments = response.json()["transcript_segments"]
    assert [segment["chunk_index"] for segment in segments] == [0, 1]
    assert segments[0]["text"] == "first"
    assert segments[0]["timed_units"] in (None, [])
    assert [unit["start_ms"] for unit in segments[1]["timed_units"]] == [1000, 1500]
    assert db.list_transcript_chunks("asset-1") == persisted_chunks


def test_asset_events_returns_not_found_for_missing_asset(client: TestClient) -> None:
    response = client.get("/api/assets/missing/events", headers=auth_headers(client))

    assert response.status_code == 404
    assert response.json() == {"detail": "Asset not found"}


def test_protected_media_gets_require_bearer_token(client: TestClient) -> None:
    missing_response = client.get("/api/assets/missing/media")
    authenticated_response = client.get(
        "/api/assets/missing/media",
        headers=auth_headers(client),
    )

    assert missing_response.status_code == 401
    assert missing_response.json() == {"detail": "Missing bearer token"}
    assert authenticated_response.status_code == 404
    assert authenticated_response.json() == {"detail": "Asset not found"}


@pytest.mark.parametrize(
    ("media_type", "expected_content_type"),
    [
        ("audio", "audio/mp4"),
        ("video", "video/mp4"),
    ],
)
def test_selected_audio_track_uses_asset_media_content_type(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    media_type: str,
    expected_content_type: str,
) -> None:
    extension = "m4a" if media_type == "audio" else "mp4"
    original_path = tmp_path / f"clip.{extension}"
    original_path.write_bytes(b"source media")
    database().create_asset(
        NewAsset(f"{media_type}-asset", original_path.name, media_type, original_path)
    )
    monkeypatch.setattr(
        "stt_vault.routes.assets.media.playback_media_stream_command",
        lambda *_args: ["ffmpeg", "-version"],
    )
    monkeypatch.setattr(
        "stt_vault.routes.assets.media.stream_process_stdout",
        lambda *_args, **_kwargs: iter([b"fragmented mp4"]),
    )

    response = client.get(
        f"/api/assets/{media_type}-asset/media?audio_track=0",
        headers=auth_headers(client),
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == expected_content_type
    assert response.content == b"fragmented mp4"


def test_mutating_routes_require_bearer_auth(client: TestClient) -> None:
    missing_response = client.post("/api/speakers/recompute")
    authenticated_response = client.post(
        "/api/speakers/recompute",
        headers=auth_headers(client),
    )

    assert missing_response.status_code == 401
    assert authenticated_response.status_code == 200
    assert authenticated_response.json() == {"assets": 0}
