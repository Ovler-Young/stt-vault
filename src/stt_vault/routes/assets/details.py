import logging
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException

from stt_vault.core.auth import require_admin
from stt_vault.core.config import Settings
from stt_vault.core.diagnostics.logging import log_exception_diagnostic
from stt_vault.core.diagnostics.process import format_diagnostic_text
from stt_vault.core.models.api import (
    AssetResponse,
    AssetSummaryResponse,
    DiarizationSegment,
    ErrorResponse,
    EventResponse,
    JobResponse,
    TranscriptResponse,
    VisualEventResponse,
)
from stt_vault.core.models.records import AssetRecord, ErrorRecord, JobEventRecord, JobRecord
from stt_vault.persistence.sqlite_database import SqliteDatabase
from stt_vault.processing.summary_service import (
    CompletedTranscriptRequiredError,
    generate_asset_summary,
    require_completed_transcript,
)

from .lookup import get_asset_or_404

__all__ = [
    "register_asset_detail_routes",
    "register_asset_event_routes",
    "register_asset_summary_routes",
]
logger = logging.getLogger(__name__)


def register_asset_detail_routes(
    app: FastAPI, settings: Settings, database: SqliteDatabase
) -> None:
    router = APIRouter()

    @router.get("/api/assets/{asset_id}")
    def get_asset(
        asset_id: str,
        _: Annotated[None, Depends(require_admin)],
        include_event_history: bool = True,
    ) -> AssetResponse:
        asset = database.get_asset(asset_id, include_event_history=include_event_history)
        if asset is None:
            raise HTTPException(status_code=404, detail="Asset not found")
        return asset_response(asset)

    app.include_router(router)


def register_asset_summary_routes(
    app: FastAPI, settings: Settings, database: SqliteDatabase
) -> None:
    router = APIRouter()

    @router.post(
        "/api/assets/{asset_id}/summary",
        dependencies=[Depends(require_admin)],
        response_model=AssetSummaryResponse,
    )
    def summarize_asset(asset_id: str) -> AssetSummaryResponse:
        asset = get_asset_or_404(database, asset_id)
        try:
            require_completed_transcript(asset)
            return AssetSummaryResponse.model_validate(
                generate_asset_summary(settings, asset_id, asset, database=database)
            )
        except CompletedTranscriptRequiredError:
            raise HTTPException(
                status_code=409, detail="A completed transcript is required"
            ) from None
        except Exception as exc:
            log_exception_diagnostic(
                logger,
                "asset summary generation failed",
                exc,
                event_name="assets.summary_generation_failed",
                context={"asset_id": asset_id},
            )
            raise HTTPException(status_code=502, detail="Summary generation failed") from exc

    app.include_router(router)


def register_asset_event_routes(app: FastAPI, settings: Settings, database: SqliteDatabase) -> None:
    router = APIRouter()

    @router.get("/api/assets/{asset_id}/events")
    def get_asset_events(
        asset_id: str, _: Annotated[None, Depends(require_admin)]
    ) -> list[EventResponse]:
        if not database.asset_exists(asset_id):
            raise HTTPException(status_code=404, detail="Asset not found")
        return [_event_response(event) for event in database.list_events(asset_id)]

    app.include_router(router)


def asset_response(asset: AssetRecord) -> AssetResponse:
    return AssetResponse(
        id=asset.id,
        filename=asset.filename,
        media_type=asset.media_type,
        status=asset.status,
        created_at=asset.created_at,
        updated_at=asset.updated_at,
        title=asset.title,
        recorded_at=asset.recorded_at,
        duration=asset.duration,
        error=_error_response(asset.error),
        diarization_stats=(
            asset.diarization_stats.as_dict() if asset.diarization_stats is not None else None
        ),
        raw_segments=[
            DiarizationSegment(start=segment.start, end=segment.end, speaker=segment.speaker)
            for segment in asset.raw_segments
        ],
        merged_segments=[
            DiarizationSegment(start=segment.start, end=segment.end, speaker=segment.speaker)
            for segment in asset.merged_segments
        ],
        speaker_centroids={name: list(values) for name, values in asset.speaker_centroids.entries},
        transcript_segments=[
            _transcript_response(segment) for segment in asset.transcript_segments
        ],
        exports={
            name: value for name, value in asset.exports.__dict__.items() if value is not None
        },
        summary_status=asset.summary_status,
        summary_text=asset.summary_text,
        summary_error=asset.summary_error,
        summary_model=asset.summary_model,
        summary_updated_at=asset.summary_updated_at,
        job=job_response(asset.job) if asset.job is not None else None,
        events=[_event_response(event) for event in asset.events]
        if asset.events is not None
        else None,
        event_history=(
            [_event_response(event) for event in asset.event_history]
            if asset.event_history is not None
            else None
        ),
        visual_events=[
            VisualEventResponse(
                event_index=event.event_index,
                timestamp=event.timestamp,
                score=event.score,
                kind=event.kind,
                created_at=event.created_at,
            )
            for event in asset.visual_events
        ],
    )


def job_response(job: JobRecord) -> JobResponse:
    return JobResponse(
        id=job.job_id,
        asset_id=job.asset_id,
        status=job.status,
        created_at=job.created_at,
        stage=job.stage,
        error=_error_response(job.error),
        started_at=job.started_at,
        finished_at=job.finished_at,
        progress_total_chunks=job.progress_total_chunks,
        progress_done_chunks=job.progress_done_chunks,
        progress_failed_chunks=job.progress_failed_chunks,
        next_retry_at=job.next_retry_at,
        run_attempt=job.run_attempt,
        claim_owner=job.claim_owner,
        claim_expires_at=job.claim_expires_at,
    )


def _transcript_response(segment: object) -> TranscriptResponse:
    return TranscriptResponse.model_validate(segment, from_attributes=True)


def _event_response(event: JobEventRecord) -> EventResponse:
    payload = _error_response(event.payload)
    return EventResponse(
        id=event.id,
        level=event.level,
        stage=event.stage,
        message=format_diagnostic_text(event.message),
        payload=payload.model_dump() if payload is not None else None,
        run_attempt=event.run_attempt,
        created_at=event.created_at,
    )


def _error_response(error: ErrorRecord | None) -> ErrorResponse | None:
    if error is None:
        return None
    return ErrorResponse(category=error.category, message=format_diagnostic_text(error.message))
