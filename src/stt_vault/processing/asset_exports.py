from stt_vault.core.settings import Settings
from stt_vault.persistence.db_asset_metadata import update_asset_exports
from stt_vault.persistence.db_asset_records import get_asset

from .exports import write_exports


def rewrite_asset_exports(settings: Settings, asset_ids: list[str]) -> None:
    for asset_id in asset_ids:
        asset = get_asset(settings.stt_db_path, asset_id, include_event_history=False)
        if asset is None:
            continue

        transcript_segments = asset.get("transcript_segments") or []
        raw_segments = asset.get("raw_segments") or []
        if not transcript_segments or not raw_segments:
            continue

        exports = write_exports(
            settings.exports_dir,
            asset_id,
            asset["filename"],
            transcript_segments,
            raw_segments,
            settings.parsed_export_formats,
        )
        update_asset_exports(settings.stt_db_path, asset_id, exports)
