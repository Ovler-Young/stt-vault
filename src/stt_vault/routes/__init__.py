import shutil

from fastapi import FastAPI

from stt_vault.core.config import Settings
from stt_vault.persistence.sqlite_database import SqliteDatabase
from stt_vault.services.asset_uploads import AssetUploadDependencies
from stt_vault.services.media_storage import move_upload, store_upload
from stt_vault.services.upload_sessions import UploadSessionDependencies, UploadSessionService
from stt_vault.workers.worker import Worker

from .assets.collection import register_asset_collection_routes
from .assets.details import (
    register_asset_detail_routes,
    register_asset_event_routes,
    register_asset_summary_routes,
)
from .assets.lifecycle import (
    register_asset_cleanup_routes,
    register_asset_delete_route,
    register_asset_move_route,
    register_asset_retry_route,
)
from .assets.media import register_asset_media_routes
from .assets.speakers import register_asset_speaker_routes
from .assets.visual_events import register_asset_visual_event_routes
from .folders.routes import register_folder_routes
from .speakers.routes import register_speaker_routes
from .system.routes import register_system_routes
from .uploads.routes import register_upload_routes

__all__ = ["register_api_routes"]


def register_api_routes(
    app: FastAPI, settings: Settings, worker: Worker, database: SqliteDatabase
) -> None:
    register_system_routes(app, settings)
    asset_upload_dependencies = AssetUploadDependencies(
        store_upload=store_upload,
        create_asset=database.create_asset,
        remove_asset_directory=lambda path: shutil.rmtree(path, ignore_errors=True),
    )
    register_asset_collection_routes(app, settings, database, asset_upload_dependencies)
    upload_session_dependencies = UploadSessionDependencies(
        create_upload_session=database.create_upload_session,
        get_upload_session=database.get_upload_session,
        update_upload_offset=database.update_upload_offset,
        complete_upload_session=database.complete_upload_session,
        move_upload=move_upload,
    )
    upload_sessions = UploadSessionService(settings, upload_session_dependencies)
    register_upload_routes(app, settings, upload_sessions)
    register_folder_routes(app, settings, database)
    register_speaker_routes(app, settings, database)
    register_asset_detail_routes(app, settings, database)
    register_asset_summary_routes(app, settings, database)
    register_asset_speaker_routes(app, settings, database)
    register_asset_event_routes(app, settings, database)
    register_asset_visual_event_routes(app, settings, database)
    register_asset_retry_route(app, settings, database)
    register_asset_move_route(app, settings, database)
    register_asset_cleanup_routes(app, settings, database)
    register_asset_media_routes(app, settings, database)
    register_asset_delete_route(app, settings, database)
