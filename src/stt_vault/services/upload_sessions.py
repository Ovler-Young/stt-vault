import shutil
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace
from pathlib import Path

from fastapi import HTTPException

from stt_vault.core.config import Settings
from stt_vault.core.models.api import UploadCompletionResponse
from stt_vault.core.models.records import (
    UploadResponse,
    UploadSessionCompletion,
    UploadSessionCreate,
    UploadSessionRecord,
)

CreateUploadSession = Callable[[UploadSessionCreate], UploadSessionRecord]
GetUploadSession = Callable[[str], UploadSessionRecord | None]
UpdateUploadOffset = Callable[[str, int], None]
CompleteUploadSession = Callable[[UploadSessionCompletion], None]
MoveUpload = Callable[[Path, str, Path], tuple[str, Path, str]]


@dataclass(frozen=True)
class UploadSessionDependencies:
    create_upload_session: CreateUploadSession
    get_upload_session: GetUploadSession
    update_upload_offset: UpdateUploadOffset
    complete_upload_session: CompleteUploadSession
    move_upload: MoveUpload


class UploadSessionService:
    def __init__(self, settings: Settings, dependencies: UploadSessionDependencies) -> None:
        self.settings = settings
        self.dependencies = dependencies

    def create(self, filename: str, total_size: int) -> UploadResponse:
        upload = self.dependencies.create_upload_session(
            UploadSessionCreate(filename, total_size, self.settings.uploads_dir)
        )
        return upload_response(upload)

    def get(self, upload_id: str) -> UploadResponse:
        return upload_response(self.require(upload_id))

    async def append(
        self, upload_id: str, start: int, end: int, total: int, body: AsyncIterator[bytes]
    ) -> UploadResponse:
        upload = self.require(upload_id)
        expected_offset = upload.offset
        if total != upload.total_size:
            raise HTTPException(status_code=409, detail="Upload size does not match session")
        if start != expected_offset:
            raise HTTPException(
                status_code=409, detail=f"Expected range to start at byte {expected_offset}"
            )
        if end >= total:
            raise HTTPException(status_code=416, detail="Content-Range exceeds upload size")

        temp_path = Path(upload.temp_path)
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
                async for chunk in body:
                    received += len(chunk)
                    if received > expected_size:
                        raise HTTPException(
                            status_code=400, detail="Content-Range does not match body size"
                        )
                    output.write(chunk)
                if received != expected_size:
                    raise HTTPException(
                        status_code=400, detail="Content-Range does not match body size"
                    )
            except Exception:
                output.truncate(expected_offset)
                raise
        next_offset = end + 1
        self.dependencies.update_upload_offset(upload_id, next_offset)
        return upload_response(replace(upload, offset=next_offset))

    def complete(self, upload_id: str) -> UploadCompletionResponse:
        upload = self.require(upload_id)
        total_size = upload.total_size
        if upload.offset != total_size:
            raise HTTPException(status_code=409, detail="Upload is incomplete")
        temp_path = Path(upload.temp_path)
        if not temp_path.is_file() or temp_path.stat().st_size != total_size:
            raise HTTPException(status_code=409, detail="Stored upload size is inconsistent")
        try:
            asset_id, stored_path, media_type = self.dependencies.move_upload(
                self.settings.media_dir, upload.filename, temp_path
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail="Upload could not be stored") from exc
        try:
            self.dependencies.complete_upload_session(
                UploadSessionCompletion(upload_id, asset_id, media_type, stored_path)
            )
        except Exception:
            if stored_path.exists():
                stored_path.replace(temp_path)
            shutil.rmtree(self.settings.media_dir / asset_id, ignore_errors=True)
            raise
        return UploadCompletionResponse(id=asset_id, status="queued")

    def require(self, upload_id: str) -> UploadSessionRecord:
        upload = self.dependencies.get_upload_session(upload_id)
        if upload is None:
            raise HTTPException(status_code=404, detail="Upload not found")
        return upload


def upload_response(upload: UploadSessionRecord) -> UploadResponse:
    return UploadResponse(upload.id, upload.filename, upload.total_size, upload.offset)
