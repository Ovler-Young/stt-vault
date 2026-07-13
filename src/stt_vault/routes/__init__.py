from fastapi import FastAPI

from ..settings import Settings
from ..worker import Worker
from .asset_media import register_asset_media_routes
from .asset_speakers import register_asset_speaker_routes
from .asset_visual_events import register_asset_visual_event_routes
from .assets import (
    register_asset_cleanup_routes,
    register_asset_collection_routes,
    register_asset_delete_route,
    register_asset_detail_routes,
    register_asset_event_routes,
    register_asset_retry_route,
    register_asset_summary_routes,
)
from .speakers import register_speaker_routes
from .system import register_system_routes

__all__ = ["register_api_routes"]


def register_api_routes(app: FastAPI, settings: Settings, worker: Worker) -> None:
    register_system_routes(app, settings)
    register_asset_collection_routes(app, settings)
    register_speaker_routes(app, settings)
    register_asset_detail_routes(app, settings)
    register_asset_summary_routes(app, settings)
    register_asset_speaker_routes(app, settings)
    register_asset_event_routes(app, settings)
    register_asset_visual_event_routes(app, settings)
    register_asset_retry_route(app, settings)
    register_asset_cleanup_routes(app, settings)
    register_asset_media_routes(app, settings)
    register_asset_delete_route(app, settings)
