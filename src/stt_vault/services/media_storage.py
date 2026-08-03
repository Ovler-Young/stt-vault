import shutil
import uuid
from pathlib import Path

from stt_vault.processing.media_probe import ffprobe_media_type


def new_asset_id() -> str:
    return uuid.uuid4().hex[:16]


def safe_filename(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in name).strip()
    return cleaned or "upload"


def _upload_destination(
    data_media_dir: Path, filename: str, source_path: Path
) -> tuple[str, Path, Path, str]:
    asset_id = new_asset_id()
    target_dir = data_media_dir / asset_id
    stored_path = target_dir / safe_filename(filename)
    return asset_id, target_dir, stored_path, ffprobe_media_type(source_path)


def store_upload(data_media_dir: Path, filename: str, source_path: Path) -> tuple[str, Path, str]:
    asset_id, target_dir, stored_path, media_type = _upload_destination(
        data_media_dir, filename, source_path
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_path, stored_path)
    return asset_id, stored_path, media_type


def move_upload(data_media_dir: Path, filename: str, source_path: Path) -> tuple[str, Path, str]:
    asset_id, target_dir, stored_path, media_type = _upload_destination(
        data_media_dir, filename, source_path
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    source_path.replace(stored_path)
    return asset_id, stored_path, media_type
