import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from stt_vault.services import asset_uploads


def test_store_asset_upload_accepts_transport_neutral_chunk_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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

    monkeypatch.setattr(asset_uploads, "store_upload", fake_store_upload)

    def fake_create_asset(*args: object) -> None:
        persisted["db_args"] = args

    monkeypatch.setattr(asset_uploads.db, "create_asset", fake_create_asset)

    asset_id = asyncio.run(asset_uploads.store_asset_upload(read_chunk, "clip.wav", settings))

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

    async def exercise() -> None:
        with pytest.raises(asset_uploads.AssetUploadTooLargeError, match="too large"):
            await asset_uploads.store_asset_upload(read_chunk, "clip.wav", settings)

    asyncio.run(exercise())
