from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stt_vault.core.app import create_app
from stt_vault.core.config import get_settings
from stt_vault.core.models.api import SpeakerResponse
from stt_vault.core.models.records import SpeakerUpsert
from stt_vault.persistence.sqlite_database import SqliteDatabase


@pytest.fixture
def route_client(
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
        yield client, {"Authorization": f"Bearer {token_response.json()['access_token']}"}
    get_settings.cache_clear()


def test_speaker_routes_return_declared_response_shapes(
    route_client: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = route_client
    settings = get_settings()
    database = SqliteDatabase(settings.stt_db_path)
    database.upsert_speaker(
        SpeakerUpsert("speaker-a", "Alice", [0.1, 0.2], 2, settings.senko_embedding_space)
    )
    database.upsert_speaker(
        SpeakerUpsert("speaker-b", "Bob", [0.3, 0.4], 1, settings.senko_embedding_space)
    )

    listed = client.get("/api/speakers", headers=headers)
    expected_listed = [
        database.get_speaker("speaker-a"),
        database.get_speaker("speaker-b"),
    ]
    renamed = client.put(
        "/api/speakers/speaker-a",
        headers=headers,
        json={"display_name": "Alicia"},
    )
    renamed_speaker = database.get_speaker("speaker-a")
    merged = client.post(
        "/api/speakers/speaker-a/merge",
        headers=headers,
        json={"source_speaker_id": "speaker-b"},
    )
    recomputed = client.post("/api/speakers/recompute", headers=headers)
    deleted = client.delete("/api/speakers/speaker-a", headers=headers)

    assert listed.status_code == 200
    assert all(speaker is not None for speaker in expected_listed)
    assert listed.json() == [
        SpeakerResponse.model_validate(speaker).model_dump() for speaker in expected_listed
    ]
    assert renamed.status_code == 200
    assert renamed_speaker is not None
    assert renamed.json() == SpeakerResponse.model_validate(renamed_speaker).model_dump()
    assert merged.status_code == 200
    assert merged.json()["id"] == "speaker-a"
    assert recomputed.json() == {"assets": 0}
    assert recomputed.status_code == 200
    assert deleted.json() == {"status": "deleted"}
    assert deleted.status_code == 200


def test_speaker_and_lifecycle_routes_reject_missing_rows(
    route_client: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = route_client

    speaker = client.put(
        "/api/speakers/missing",
        headers=headers,
        json={"display_name": "Alice"},
    )
    asset = client.post(
        "/api/assets/missing/move",
        headers=headers,
        json={"parent_folder_id": None},
    )
    cleanup = client.post("/api/assets/missing/cleanup", headers=headers)

    assert speaker.status_code == 404
    assert speaker.json() == {"detail": "Speaker not found"}
    assert asset.status_code == 404
    assert asset.json() == {"detail": "Asset not found"}
    assert cleanup.status_code == 404
    assert cleanup.json() == {"detail": "Cleanup task not found"}


def test_speaker_rename_rejects_a_missing_post_mutation_row(
    route_client: tuple[TestClient, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, headers = route_client
    settings = get_settings()
    database = SqliteDatabase(settings.stt_db_path)
    database.upsert_speaker(
        SpeakerUpsert("speaker-a", "Alice", [0.1, 0.2], 1, settings.senko_embedding_space)
    )
    original_get_speaker = SqliteDatabase.get_speaker
    calls = 0

    def get_speaker_then_lose_row(database: SqliteDatabase, speaker_id: str):
        nonlocal calls
        calls += 1
        return original_get_speaker(database, speaker_id) if calls == 1 else None

    monkeypatch.setattr(SqliteDatabase, "get_speaker", get_speaker_then_lose_row)

    response = client.put(
        "/api/speakers/speaker-a",
        headers=headers,
        json={"display_name": "Alicia"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Speaker not found"}
