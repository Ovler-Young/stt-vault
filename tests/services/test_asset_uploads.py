import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from stt_vault.services import asset_uploads


def test_store_asset_upload_uses_injected_persistence_dependencies(tmp_path: Path) -> None:
    chunks = iter([b"audio", b""])
    persisted: dict[str, object] = {}
    settings = SimpleNamespace(
        media_dir=tmp_path / "media",
        stt_db_path=tmp_path / "stt.db",
        max_upload_bytes=1024,
    )

    async def read_chunk(_size: int) -> bytes:
        return next(chunks)

    def fake_store_upload(_media_dir: Path, filename: str, source_path: Path):
        persisted["filename"] = filename
        persisted["bytes"] = source_path.read_bytes()
        return "asset-1", tmp_path / "media" / "asset-1" / filename, "audio"

    def fake_create_asset(*args: object) -> None:
        persisted["db_args"] = args

    dependencies = asset_uploads.AssetUploadDependencies(
        store_upload=fake_store_upload,
        create_asset=fake_create_asset,
        remove_asset_directory=lambda _path: None,
    )

    asset_id = asyncio.run(
        asset_uploads.store_asset_upload(read_chunk, "clip.wav", settings, dependencies)
    )

    assert asset_id == "asset-1"
    assert persisted["filename"] == "clip.wav"
    assert persisted["bytes"] == b"audio"
    assert persisted["db_args"][2] == "clip.wav"


def test_store_asset_upload_raises_domain_error_for_oversized_reader(
    tmp_path: Path,
) -> None:
    async def read_chunk(_size: int) -> bytes:
        return b"12345"

    settings = SimpleNamespace(
        media_dir=tmp_path / "media",
        stt_db_path=tmp_path / "stt.db",
        max_upload_bytes=4,
    )
    dependencies = asset_uploads.AssetUploadDependencies(
        store_upload=lambda *_args: pytest.fail("Storage must not be called"),
        create_asset=lambda *_args: pytest.fail("Persistence must not be called"),
        remove_asset_directory=lambda _path: pytest.fail("Rollback must not be called"),
    )

    async def exercise() -> None:
        with pytest.raises(asset_uploads.AssetUploadTooLargeError, match="too large"):
            await asset_uploads.store_asset_upload(read_chunk, "clip.wav", settings, dependencies)

    asyncio.run(exercise())


def test_store_asset_upload_rolls_back_through_injected_filesystem_dependency(
    tmp_path: Path,
) -> None:
    settings = SimpleNamespace(
        media_dir=tmp_path / "media",
        stt_db_path=tmp_path / "stt.db",
        max_upload_bytes=1024,
    )
    removed_directories: list[Path] = []
    chunks = iter([b"audio", b""])

    async def read_chunk(_size: int) -> bytes:
        return next(chunks)

    def fail_create_asset(*_args: object) -> None:
        raise OSError("database unavailable")

    dependencies = asset_uploads.AssetUploadDependencies(
        store_upload=lambda *_args: (
            "asset-1",
            settings.media_dir / "asset-1" / "clip.wav",
            "audio",
        ),
        create_asset=fail_create_asset,
        remove_asset_directory=removed_directories.append,
    )

    with pytest.raises(asset_uploads.AssetUploadPersistenceError, match="could not be stored"):
        asyncio.run(
            asset_uploads.store_asset_upload(read_chunk, "clip.wav", settings, dependencies)
        )

    assert removed_directories == [settings.media_dir / "asset-1"]
