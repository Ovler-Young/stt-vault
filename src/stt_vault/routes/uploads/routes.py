import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request

from stt_vault.core.auth import require_admin
from stt_vault.core.config import Settings
from stt_vault.core.models.api import UploadCompletionResponse, UploadProgressResponse
from stt_vault.core.models.records import UploadResponse
from stt_vault.core.models.requests import UploadCreateRequest
from stt_vault.services.upload_sessions import UploadSessionService

from ..assets.collection import validate_relative_path

CONTENT_RANGE_PATTERN = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$")


@dataclass
class _ActiveUploadLock:
    lock: asyncio.Lock
    users: int = 0


class UploadLockRegistry:
    def __init__(self) -> None:
        self._locks: dict[str, _ActiveUploadLock] = {}

    @asynccontextmanager
    async def acquire(self, upload_id: str) -> AsyncIterator[None]:
        active_lock = self._locks.setdefault(upload_id, _ActiveUploadLock(asyncio.Lock()))
        active_lock.users += 1
        try:
            async with active_lock.lock:
                yield
        finally:
            active_lock.users -= 1
            if active_lock.users == 0:
                del self._locks[upload_id]


def register_upload_routes(
    app: FastAPI,
    settings: Settings,
    sessions: UploadSessionService,
    lock_registry: UploadLockRegistry | None = None,
) -> None:
    router = APIRouter(dependencies=[Depends(require_admin)])
    if lock_registry is None:
        lock_registry = UploadLockRegistry()

    @router.post("/api/uploads", response_model=UploadProgressResponse)
    def create_upload(payload: UploadCreateRequest) -> UploadResponse:
        filename = validate_relative_path(payload.filename)
        if payload.size > settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="Upload is too large")
        return sessions.create(filename, payload.size)

    @router.get("/api/uploads/{upload_id}", response_model=UploadProgressResponse)
    def get_upload(upload_id: str) -> UploadResponse:
        return sessions.get(upload_id)

    @router.put("/api/uploads/{upload_id}", response_model=UploadProgressResponse)
    async def put_upload_range(
        upload_id: str,
        request: Request,
        content_range: str = Header(alias="Content-Range"),
    ) -> UploadResponse:
        async with lock_registry.acquire(upload_id):
            start, end, total = _parse_content_range(content_range)
            return await sessions.append(upload_id, start, end, total, request.stream())

    @router.post("/api/uploads/{upload_id}/complete", response_model=UploadCompletionResponse)
    async def complete_upload(upload_id: str) -> UploadCompletionResponse:
        async with lock_registry.acquire(upload_id):
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
