from fastapi import FastAPI

from stt_vault.core.settings import Settings
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


def register_api_routes(app: FastAPI, settings: Settings, worker: Worker) -> None:
    register_system_routes(app, settings)
    register_asset_collection_routes(app, settings)
    register_upload_routes(app, settings)
    register_folder_routes(app, settings)
    register_speaker_routes(app, settings)
    register_asset_detail_routes(app, settings)
    register_asset_summary_routes(app, settings)
    register_asset_speaker_routes(app, settings)
    register_asset_event_routes(app, settings)
    register_asset_visual_event_routes(app, settings)
    register_asset_retry_route(app, settings)
    register_asset_move_route(app, settings)
    register_asset_cleanup_routes(app, settings)
    register_asset_media_routes(app, settings)
    register_asset_delete_route(app, settings)
