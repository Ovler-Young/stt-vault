from pathlib import Path

from fastapi import HTTPException

from stt_vault.core.types import AssetRecord
from stt_vault.persistence import db


def get_asset_or_404(db_path: Path, asset_id: str) -> AssetRecord:
    asset = db.get_asset(db_path, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset
