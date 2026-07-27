from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException

from stt_vault.core.api_models import (
    FolderDeleteResponse,
    FolderResponse,
    FolderTreeResponse,
)
from stt_vault.core.auth import require_admin
from stt_vault.core.requests import FolderCreateRequest, FolderMoveRequest, FolderRenameRequest
from stt_vault.core.settings import Settings
from stt_vault.persistence import db
from stt_vault.persistence.db_folders import FolderDataIntegrityError

__all__ = ["register_folder_routes"]


def register_folder_routes(app: FastAPI, settings: Settings) -> None:
    router = APIRouter()

    @router.get("/api/folders", response_model=FolderTreeResponse)
    def list_folder_tree(_: Annotated[None, Depends(require_admin)]) -> FolderTreeResponse:
        try:
            return db.list_folder_tree(settings.stt_db_path)
        except FolderDataIntegrityError:
            raise HTTPException(status_code=500, detail="Folder data is invalid") from None

    @router.post(
        "/api/folders",
        dependencies=[Depends(require_admin)],
        response_model=FolderResponse,
    )
    def create_folder(payload: FolderCreateRequest) -> FolderResponse:
        try:
            return db.create_folder(
                settings.stt_db_path,
                payload.name,
                parent_id=payload.parent_id,
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="Parent folder not found") from None
        except FolderDataIntegrityError:
            raise HTTPException(status_code=500, detail="Folder data is invalid") from None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post(
        "/api/folders/{folder_id}/move",
        dependencies=[Depends(require_admin)],
        response_model=FolderResponse,
    )
    def move_folder(folder_id: str, payload: FolderMoveRequest) -> FolderResponse:
        try:
            return db.move_folder(settings.stt_db_path, folder_id, payload.parent_id)
        except FolderDataIntegrityError:
            raise HTTPException(status_code=500, detail="Folder data is invalid") from None
        except KeyError as exc:
            detail = "Folder not found" if exc.args[0] == folder_id else "Parent folder not found"
            raise HTTPException(status_code=404, detail=detail) from None
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.put(
        "/api/folders/{folder_id}",
        dependencies=[Depends(require_admin)],
        response_model=FolderResponse,
    )
    def rename_folder(folder_id: str, payload: FolderRenameRequest) -> FolderResponse:
        try:
            return db.rename_folder(settings.stt_db_path, folder_id, payload.name)
        except FolderDataIntegrityError:
            raise HTTPException(status_code=500, detail="Folder data is invalid") from None
        except KeyError:
            raise HTTPException(status_code=404, detail="Folder not found") from None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.delete(
        "/api/folders/{folder_id}",
        dependencies=[Depends(require_admin)],
        response_model=FolderDeleteResponse,
    )
    def delete_folder(folder_id: str) -> FolderDeleteResponse:
        try:
            db.delete_folder(settings.stt_db_path, folder_id)
        except FolderDataIntegrityError:
            raise HTTPException(status_code=500, detail="Folder data is invalid") from None
        except KeyError:
            raise HTTPException(status_code=404, detail="Folder not found") from None
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return FolderDeleteResponse(status="deleted")

    app.include_router(router)
