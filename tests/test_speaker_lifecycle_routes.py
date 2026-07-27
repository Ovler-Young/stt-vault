from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stt_vault.core.app import create_app
from stt_vault.core.settings import get_settings
from stt_vault.persistence import db
from stt_vault.persistence.db_asset_relocation import AssetNotFoundError


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
    expected_listed = [
        db.get_speaker(settings.stt_db_path, "speaker-a"),
        db.get_speaker(settings.stt_db_path, "speaker-b"),
    ]
    renamed = client.put(
        "/api/speakers/speaker-a",
        headers=headers,
        json={"display_name": "Alicia"},
    )
    renamed_speaker = db.get_speaker(settings.stt_db_path, "speaker-a")
    merged = client.post(
        "/api/speakers/speaker-a/merge",
        headers=headers,
        json={"source_speaker_id": "speaker-b"},
    )
    recomputed = client.post("/api/speakers/recompute", headers=headers)
    deleted = client.delete("/api/speakers/speaker-a", headers=headers)

    assert listed.status_code == 200
    assert all(speaker is not None for speaker in expected_listed)
    assert listed.json() == expected_listed
    assert renamed.status_code == 200
    assert renamed_speaker is not None
    assert renamed.json() == renamed_speaker
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
    assert set(saved.json()) == {
        "id",
        "display_name",
        "centroid",
        "sample_count",
        "created_at",
        "updated_at",
    }
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


def test_asset_lifecycle_mutations_do_not_preflight_load_assets(
    route_client: tuple[TestClient, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, headers = route_client
    settings = get_settings()
    for asset_id in ("asset-retry", "asset-move", "asset-delete"):
        db.create_asset(
            settings.stt_db_path,
            asset_id,
            "recording.wav",
            "audio",
            settings.media_dir / asset_id / "recording.wav",
        )

    def fail_preflight_load(db_path: Path, asset_id: str):
        raise AssertionError(f"unexpected asset preflight for {asset_id}")

    monkeypatch.setattr(db, "get_asset", fail_preflight_load)

    retried = client.post("/api/assets/asset-retry/retry", headers=headers)
    moved = client.post(
        "/api/assets/asset-move/move",
        headers=headers,
        json={"parent_folder_id": None},
    )
    deleted = client.delete("/api/assets/asset-delete", headers=headers)

    assert retried.status_code == 200
    assert retried.json() == {"status": "queued"}
    assert moved.status_code == 200
    assert moved.json()["id"] == "asset-move"
    assert deleted.status_code == 200
    assert deleted.json() == {"status": "deleted"}


def test_asset_lifecycle_mutations_report_missing_assets_and_folders(
    route_client: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = route_client
    retry = client.post("/api/assets/missing-retry/retry", headers=headers)
    move = client.post(
        "/api/assets/missing-move/move",
        headers=headers,
        json={"parent_folder_id": None},
    )
    delete = client.delete("/api/assets/missing-delete", headers=headers)
    settings = get_settings()
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

    assert retry.status_code == 404
    assert retry.json() == {"detail": "Asset not found"}
    assert move.status_code == 404
    assert move.json() == {"detail": "Asset not found"}
    assert delete.status_code == 404
    assert delete.json() == {"detail": "Asset not found"}
    assert missing_folder.status_code == 404
    assert missing_folder.json() == {"detail": "Folder not found"}


def test_asset_move_reports_a_disappearing_asset_at_mutation_time(
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
    original_move_asset = db.move_asset

    def delete_then_move(db_path: Path, asset_id: str, parent_folder_id: str | None):
        db.delete_asset_with_cleanup_task(
            db_path,
            asset_id,
            settings.media_dir / asset_id,
            settings.exports_dir / asset_id,
        )
        return original_move_asset(db_path, asset_id, parent_folder_id)

    monkeypatch.setattr(db, "move_asset", delete_then_move)

    response = client.post(
        "/api/assets/asset-disappears/move",
        headers=headers,
        json={"parent_folder_id": None},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Asset not found"}


def test_asset_move_does_not_map_unrelated_key_errors_to_missing_folders(
    route_client: tuple[TestClient, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, headers = route_client

    def fail_move(db_path: Path, asset_id: str, parent_folder_id: str | None):
        raise KeyError("unexpected persistence failure")

    monkeypatch.setattr(db, "move_asset", fail_move)

    response = client.post(
        "/api/assets/asset-unrelated-error/move",
        headers=headers,
        json={"parent_folder_id": None},
    )

    assert response.status_code == 500


def test_asset_mutation_persistence_operations_raise_typed_missing_asset_errors(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "stt.sqlite3"
    db.initialize(db_path)

    with pytest.raises(AssetNotFoundError):
        db.retry_asset(db_path, "missing-retry")
    with pytest.raises(AssetNotFoundError):
        db.move_asset(db_path, "missing-move", None)
    with pytest.raises(AssetNotFoundError):
        db.delete_asset_with_cleanup_task(
            db_path,
            "missing-delete",
            tmp_path / "media",
            tmp_path / "exports",
        )


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
