from fastapi import HTTPException

from stt_vault.core.models.records import AssetRecord
from stt_vault.persistence.sqlite_database import SqliteDatabase


def get_asset_or_404(
    database: SqliteDatabase,
    asset_id: str,
) -> AssetRecord:
    asset = database.get_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset
