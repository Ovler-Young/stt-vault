import logging
import shutil
import tempfile
from pathlib import Path

from fastapi import HTTPException, UploadFile

from stt_vault.core.config import Settings
from stt_vault.core.diagnostics.logging import log_exception_diagnostic
from stt_vault.persistence import db
from stt_vault.services.media_storage import store_upload

__all__ = ["store_asset_upload"]
logger = logging.getLogger(__name__)


async def store_asset_upload(file: UploadFile, filename: str, settings: Settings) -> str:
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = Path(tmp.name)
        copied = 0
        max_bytes = settings.max_upload_bytes
        while chunk := await file.read(1024 * 1024):
            copied += len(chunk)
            if copied > max_bytes:
                tmp_path.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="Upload is too large")
            tmp.write(chunk)
    try:
        asset_id, stored_path, media_type = store_upload(settings.media_dir, filename, tmp_path)
        try:
            db.create_asset(settings.stt_db_path, asset_id, filename, media_type, stored_path)
        except Exception:
            shutil.rmtree(settings.media_dir / asset_id, ignore_errors=True)
            raise
        return asset_id
    except HTTPException:
        raise
    except Exception as exc:
        log_exception_diagnostic(
            logger,
            "upload persistence failed",
            exc,
            event_name="upload.persistence_failed",
            context={"upload_filename": filename},
        )
        raise HTTPException(status_code=500, detail="Upload could not be stored") from exc
    finally:
        tmp_path.unlink(missing_ok=True)
