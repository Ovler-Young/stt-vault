from pathlib import Path

from fastapi import HTTPException

from stt_vault.core.models.records import AssetRecord
from stt_vault.persistence import db


def get_asset_or_404(
    db_path: Path,
    asset_id: str,
    *,
    include_event_history: bool = True,
) -> AssetRecord:
    asset = db.get_asset(db_path, asset_id, include_event_history=include_event_history)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset
