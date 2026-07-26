import asyncio
import re

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request

from stt_vault.core.auth import require_admin
from stt_vault.core.requests import UploadCreateRequest
from stt_vault.core.settings import Settings
from stt_vault.core.types import UploadResponse
from stt_vault.services.upload_sessions import UploadSessionService

from .assets import _validated_relative_path

CONTENT_RANGE_PATTERN = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$")
UPLOAD_LOCKS: dict[str, asyncio.Lock] = {}


def register_upload_routes(app: FastAPI, settings: Settings) -> None:
    router = APIRouter(dependencies=[Depends(require_admin)])
    sessions = UploadSessionService(settings)

    @router.post("/api/uploads")
    def create_upload(payload: UploadCreateRequest) -> UploadResponse:
        filename = _validated_relative_path(payload.filename)
        if payload.size > settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="Upload is too large")
        return sessions.create(filename, payload.size)

    @router.get("/api/uploads/{upload_id}")
    def get_upload(upload_id: str) -> UploadResponse:
        return sessions.get(upload_id)

    @router.put("/api/uploads/{upload_id}")
    async def put_upload_range(
        upload_id: str,
        request: Request,
        content_range: str = Header(alias="Content-Range"),
    ) -> UploadResponse:
        async with _upload_lock(upload_id):
            start, end, total = _parse_content_range(content_range)
            return await sessions.append(upload_id, start, end, total, request.stream())

    @router.post("/api/uploads/{upload_id}/complete")
    async def complete_upload(upload_id: str) -> dict[str, str]:
        async with _upload_lock(upload_id):
            return sessions.complete(upload_id)

    app.include_router(router)


def _parse_content_range(value: str) -> tuple[int, int, int]:
    match = CONTENT_RANGE_PATTERN.fullmatch(value.strip())
    if match is None:
        raise HTTPException(status_code=400, detail="Content-Range is invalid")
    start, end, total = (int(part) for part in match.groups())
    if start > end or total <= 0:
        raise HTTPException(status_code=400, detail="Content-Range is invalid")
    return start, end, total


def _upload_lock(upload_id: str) -> asyncio.Lock:
    return UPLOAD_LOCKS.setdefault(upload_id, asyncio.Lock())
