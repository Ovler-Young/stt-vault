from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException

from stt_vault.core.api_models import AssetResponse, AssetSummaryResponse, EventResponse
from stt_vault.core.auth import require_admin
from stt_vault.core.settings import Settings
from stt_vault.persistence import db
from stt_vault.processing.summary_service import (
    CompletedTranscriptRequiredError,
    generate_asset_summary,
    require_completed_transcript,
)

from .asset_lookup import get_asset_or_404

__all__ = [
    "register_asset_detail_routes",
    "register_asset_event_routes",
    "register_asset_summary_routes",
]


def register_asset_detail_routes(app: FastAPI, settings: Settings) -> None:
    router = APIRouter()

    @router.get("/api/assets/{asset_id}")
    def get_asset(
        asset_id: str,
        _: Annotated[None, Depends(require_admin)],
        include_event_history: bool = True,
    ) -> AssetResponse:
        return get_asset_or_404(
            settings.stt_db_path,
            asset_id,
            include_event_history=include_event_history,
        )

    app.include_router(router)


def register_asset_summary_routes(app: FastAPI, settings: Settings) -> None:
    router = APIRouter()

    @router.post(
        "/api/assets/{asset_id}/summary",
        dependencies=[Depends(require_admin)],
        response_model=AssetSummaryResponse,
    )
    def summarize_asset(asset_id: str) -> AssetSummaryResponse:
        asset = get_asset_or_404(settings.stt_db_path, asset_id, include_event_history=False)
        try:
            require_completed_transcript(asset)
            return AssetSummaryResponse.model_validate(
                generate_asset_summary(settings, asset_id, asset)
            )
        except CompletedTranscriptRequiredError:
            raise HTTPException(
                status_code=409, detail="A completed transcript is required"
            ) from None
        except Exception as exc:
            raise HTTPException(status_code=502, detail="Summary generation failed") from exc

    app.include_router(router)


def register_asset_event_routes(app: FastAPI, settings: Settings) -> None:
    router = APIRouter()

    @router.get("/api/assets/{asset_id}/events")
    def get_asset_events(
        asset_id: str, _: Annotated[None, Depends(require_admin)]
    ) -> list[EventResponse]:
        if not db.asset_exists(settings.stt_db_path, asset_id):
            raise HTTPException(status_code=404, detail="Asset not found")
        return db.list_events(settings.stt_db_path, asset_id)

    app.include_router(router)
