from stt_vault.core.config import Settings
from stt_vault.persistence.sqlite_database import SqliteDatabase

from .exports import write_exports


def rewrite_asset_exports(
    settings: Settings, database: SqliteDatabase, asset_ids: list[str]
) -> None:
    for asset_id in asset_ids:
        asset = database.get_asset(asset_id)
        if asset is None:
            continue

        transcript_segments = asset.transcript_segments
        raw_segments = asset.raw_segments
        if not transcript_segments or not raw_segments:
            continue

        exports = write_exports(
            settings.exports_dir,
            asset_id,
            asset.filename,
            list(transcript_segments),
            list(raw_segments),
            settings.parsed_export_formats,
        )
        database.update_asset_exports(asset_id, exports)
