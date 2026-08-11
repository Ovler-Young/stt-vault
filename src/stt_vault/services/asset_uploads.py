import logging
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from stt_vault.core.config import Settings
from stt_vault.core.diagnostics.logging import log_exception_diagnostic
from stt_vault.core.models.records import NewAsset

__all__ = [
    "AssetUploadDependencies",
    "AssetUploadPersistenceError",
    "AssetUploadTooLargeError",
    "store_asset_upload",
]
logger = logging.getLogger(__name__)
ChunkReader = Callable[[int], Awaitable[bytes]]
StoreUpload = Callable[[Path, str, Path], tuple[str, Path, str]]
CreateAsset = Callable[[NewAsset], object]
RemoveAssetDirectory = Callable[[Path], None]


@dataclass(frozen=True)
class AssetUploadDependencies:
    store_upload: StoreUpload
    create_asset: CreateAsset
    remove_asset_directory: RemoveAssetDirectory


class AssetUploadTooLargeError(ValueError):
    """Raised when an upload exceeds the configured byte limit."""


class AssetUploadPersistenceError(RuntimeError):
    """Raised when an upload cannot be stored and committed."""


async def store_asset_upload(
    read_chunk: ChunkReader,
    filename: str,
    settings: Settings,
    dependencies: AssetUploadDependencies,
) -> str:
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = Path(tmp.name)
        copied = 0
        max_bytes = settings.max_upload_bytes
        while chunk := await read_chunk(1024 * 1024):
            copied += len(chunk)
            if copied > max_bytes:
                tmp_path.unlink(missing_ok=True)
                raise AssetUploadTooLargeError("Upload is too large")
            tmp.write(chunk)
    try:
        asset_id, stored_path, media_type = dependencies.store_upload(
            settings.media_dir, filename, tmp_path
        )
        try:
            dependencies.create_asset(NewAsset(asset_id, filename, media_type, stored_path))
        except Exception:
            dependencies.remove_asset_directory(settings.media_dir / asset_id)
            raise
        return asset_id
    except Exception as exc:
        log_exception_diagnostic(
            logger,
            "upload persistence failed",
            exc,
            event_name="upload.persistence_failed",
            context={"upload_filename": filename},
        )
        raise AssetUploadPersistenceError("Upload could not be stored") from exc
    finally:
        tmp_path.unlink(missing_ok=True)
