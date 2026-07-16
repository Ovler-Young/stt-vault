import asyncio
import re
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request

from .. import db
from ..auth import require_admin
from ..media import move_upload
from ..requests import UploadCreateRequest
from ..settings import Settings
from .assets import _validated_relative_path

CONTENT_RANGE_PATTERN = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$")
UPLOAD_LOCKS: dict[str, asyncio.Lock] = {}


def register_upload_routes(app: FastAPI, settings: Settings) -> None:
    router = APIRouter(dependencies=[Depends(require_admin)])

    @router.post("/api/uploads")
    def create_upload(payload: UploadCreateRequest) -> dict:
        filename = _validated_relative_path(payload.filename)
        if payload.size > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Upload is too large")
        upload_id = _new_upload_session(settings, filename, payload.size)
        return _upload_response(_require_upload(settings, upload_id))

    @router.get("/api/uploads/{upload_id}")
    def get_upload(upload_id: str) -> dict:
        return _upload_response(_require_upload(settings, upload_id))

    @router.put("/api/uploads/{upload_id}")
    async def put_upload_range(
        upload_id: str,
        request: Request,
        content_range: str = Header(alias="Content-Range"),
    ) -> dict:
        async with _upload_lock(upload_id):
            upload = _require_upload(settings, upload_id)
            start, end, total = _parse_content_range(content_range)
            expected_offset = int(upload["offset"])
            if total != int(upload["total_size"]):
                raise HTTPException(status_code=409, detail="Upload size does not match session")
            if start != expected_offset:
                raise HTTPException(
                    status_code=409,
                    detail=f"Expected range to start at byte {expected_offset}",
                )
            if end >= total:
                raise HTTPException(status_code=416, detail="Content-Range exceeds upload size")

            temp_path = Path(upload["temp_path"])
            temp_path.parent.mkdir(parents=True, exist_ok=True)
            actual_size = temp_path.stat().st_size if temp_path.exists() else 0
            if actual_size > expected_offset:
                with temp_path.open("r+b") as output:
                    output.truncate(expected_offset)
            elif actual_size < expected_offset:
                raise HTTPException(status_code=409, detail="Stored upload offset is inconsistent")
            expected_size = end - start + 1
            received = 0
            with temp_path.open("ab") as output:
                try:
                    async for chunk in request.stream():
                        received += len(chunk)
                        if received > expected_size:
                            raise HTTPException(
                                status_code=400,
                                detail="Content-Range does not match body size",
                            )
                        output.write(chunk)
                    if received != expected_size:
                        raise HTTPException(
                            status_code=400,
                            detail="Content-Range does not match body size",
                        )
                except Exception:
                    output.truncate(expected_offset)
                    raise
            next_offset = end + 1
            db.update_upload_offset(settings.stt_db_path, upload_id, next_offset)
            upload["offset"] = next_offset
            return _upload_response(upload)

    @router.post("/api/uploads/{upload_id}/complete")
    async def complete_upload(upload_id: str) -> dict[str, str]:
        async with _upload_lock(upload_id):
            upload = _require_upload(settings, upload_id)
            total_size = int(upload["total_size"])
            if int(upload["offset"]) != total_size:
                raise HTTPException(status_code=409, detail="Upload is incomplete")
            temp_path = Path(upload["temp_path"])
            if not temp_path.is_file() or temp_path.stat().st_size != total_size:
                raise HTTPException(status_code=409, detail="Stored upload size is inconsistent")
            asset_id, stored_path, media_type = move_upload(
                settings.media_dir,
                upload["filename"],
                temp_path,
            )
            try:
                db.complete_upload_session(
                    settings.stt_db_path,
                    upload_id,
                    asset_id,
                    media_type,
                    stored_path,
                )
            except Exception:
                if stored_path.exists():
                    stored_path.replace(temp_path)
                shutil.rmtree(settings.media_dir / asset_id, ignore_errors=True)
                raise
            return {"id": asset_id, "status": "queued"}

    app.include_router(router)


def _new_upload_session(settings: Settings, filename: str, total_size: int) -> str:
    upload = db.create_upload_session(
        settings.stt_db_path,
        filename,
        total_size,
        settings.uploads_dir,
    )
    return upload["id"]


def _require_upload(settings: Settings, upload_id: str) -> dict:
    upload = db.get_upload_session(settings.stt_db_path, upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail="Upload not found")
    return upload


def _upload_response(upload: dict) -> dict:
    return {
        "id": upload["id"],
        "filename": upload["filename"],
        "size": upload["total_size"],
        "offset": upload["offset"],
    }


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
