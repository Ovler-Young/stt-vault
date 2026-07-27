from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stt_vault.core.app import create_app
from stt_vault.core.settings import get_settings
from stt_vault.persistence import db
from stt_vault.routes import asset_lifecycle


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
    db.upsert_speaker(settings.stt_db_path, "speaker-a", "Alice", [0.1, 0.2], 2)
    db.upsert_speaker(settings.stt_db_path, "speaker-b", "Bob", [0.3, 0.4], 1)

    listed = client.get("/api/speakers", headers=headers)
    renamed = client.put(
        "/api/speakers/speaker-a",
        headers=headers,
        json={"display_name": "Alicia"},
    )
    merged = client.post(
        "/api/speakers/speaker-a/merge",
        headers=headers,
        json={"source_speaker_id": "speaker-b"},
    )
    recomputed = client.post("/api/speakers/recompute", headers=headers)
    deleted = client.delete("/api/speakers/speaker-a", headers=headers)

    assert listed.status_code == 200
    assert all(set(speaker) == {"id", "display_name", "centroid"} for speaker in listed.json())
    assert renamed.status_code == 200
    assert renamed.json() == {"id": "speaker-a", "display_name": "Alicia", "centroid": [0.1, 0.2]}
    assert merged.status_code == 200
    assert merged.json()["id"] == "speaker-a"
    assert recomputed.json() == {"assets": 0}
    assert recomputed.status_code == 200
    assert deleted.json() == {"status": "deleted"}
    assert deleted.status_code == 200


def test_asset_speaker_and_lifecycle_routes_return_declared_response_shapes(
    route_client: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = route_client
    settings = get_settings()
    db.create_asset(
        settings.stt_db_path,
        "asset-1",
        "recording.wav",
        "audio",
        settings.media_dir / "asset-1" / "recording.wav",
    )
    db.update_diarization_metadata(
        settings.stt_db_path,
        "asset-1",
        wav_path=settings.media_dir / "asset-1" / "recording.wav",
        duration=1.0,
        diarization_stats={},
        raw_segments=[],
        merged_segments=[],
        speaker_centroids={"SPEAKER_00": [0.1, 0.2]},
    )

    saved = client.post(
        "/api/assets/asset-1/speakers/SPEAKER_00",
        headers=headers,
        json={"display_name": "Alice"},
    )
    recomputed = client.post(
        "/api/assets/asset-1/speaker-matches/recompute",
        headers=headers,
    )
    retried = client.post("/api/assets/asset-1/retry", headers=headers)
    moved = client.post(
        "/api/assets/asset-1/move",
        headers=headers,
        json={"parent_folder_id": None},
    )
    deleted = client.delete("/api/assets/asset-1", headers=headers)
    db.record_cleanup_task(
        settings.stt_db_path,
        "asset-cleanup",
        settings.media_dir / "asset-cleanup",
        settings.exports_dir / "asset-cleanup",
    )
    cleanup = client.post("/api/assets/asset-cleanup/cleanup", headers=headers)

    assert saved.status_code == 200
    assert set(saved.json()) == {"id", "display_name", "centroid"}
    assert recomputed.status_code == 200
    assert recomputed.json() == {"assets": 0}
    assert retried.json() == {"status": "queued"}
    assert retried.status_code == 200
    assert set(moved.json()) == {"id", "parent_folder_id", "updated_at"}
    assert moved.status_code == 200
    assert deleted.json() == {"status": "deleted"}
    assert deleted.status_code == 200
    assert cleanup.json() == {"status": "deleted"}
    assert cleanup.status_code == 200


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


def test_asset_move_distinguishes_a_disappearing_asset_from_a_missing_folder(
    route_client: tuple[TestClient, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, headers = route_client
    settings = get_settings()
    db.create_asset(
        settings.stt_db_path,
        "asset-disappears",
        "recording.wav",
        "audio",
        settings.media_dir / "asset-disappears" / "recording.wav",
    )
    original_get_asset_or_404 = asset_lifecycle.get_asset_or_404

    def get_asset_then_delete(db_path: Path, asset_id: str):
        asset = original_get_asset_or_404(db_path, asset_id)
        db.delete_asset_with_cleanup_task(
            db_path,
            asset_id,
            settings.media_dir / asset_id,
            settings.exports_dir / asset_id,
        )
        return asset

    monkeypatch.setattr(asset_lifecycle, "get_asset_or_404", get_asset_then_delete)
    disappeared = client.post(
        "/api/assets/asset-disappears/move",
        headers=headers,
        json={"parent_folder_id": None},
    )
    monkeypatch.setattr(asset_lifecycle, "get_asset_or_404", original_get_asset_or_404)

    db.create_asset(
        settings.stt_db_path,
        "asset-folder",
        "recording.wav",
        "audio",
        settings.media_dir / "asset-folder" / "recording.wav",
    )
    missing_folder = client.post(
        "/api/assets/asset-folder/move",
        headers=headers,
        json={"parent_folder_id": "missing-folder"},
    )

    assert disappeared.status_code == 404
    assert disappeared.json() == {"detail": "Asset not found"}
    assert missing_folder.status_code == 404
    assert missing_folder.json() == {"detail": "Folder not found"}


def test_speaker_rename_rejects_a_missing_post_mutation_row(
    route_client: tuple[TestClient, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, headers = route_client
    settings = get_settings()
    db.upsert_speaker(settings.stt_db_path, "speaker-a", "Alice", [0.1, 0.2], 1)
    original_get_speaker = db.get_speaker
    calls = 0

    def get_speaker_then_lose_row(db_path: Path, speaker_id: str):
        nonlocal calls
        calls += 1
        return original_get_speaker(db_path, speaker_id) if calls == 1 else None

    monkeypatch.setattr(db, "get_speaker", get_speaker_then_lose_row)

    response = client.put(
        "/api/speakers/speaker-a",
        headers=headers,
        json={"display_name": "Alicia"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Speaker not found"}
