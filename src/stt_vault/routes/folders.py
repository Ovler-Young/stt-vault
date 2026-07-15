from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException

from .. import db
from ..auth import require_admin
from ..requests import FolderCreateRequest, FolderMoveRequest
from ..settings import Settings

__all__ = ["register_folder_routes"]


def register_folder_routes(app: FastAPI, settings: Settings) -> None:
    router = APIRouter()

    @router.get("/api/folders")
    def list_folder_tree(_: Annotated[None, Depends(require_admin)]) -> dict:
        return db.list_folder_tree(settings.stt_db_path)

    @router.post("/api/folders", dependencies=[Depends(require_admin)])
    def create_folder(payload: FolderCreateRequest) -> dict:
        try:
            return db.create_folder(
                settings.stt_db_path,
                payload.name,
                parent_id=payload.parent_id,
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="Parent folder not found") from None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/api/folders/{folder_id}/move", dependencies=[Depends(require_admin)])
    def move_folder(folder_id: str, payload: FolderMoveRequest) -> dict:
        try:
            return db.move_folder(settings.stt_db_path, folder_id, payload.parent_id)
        except KeyError as exc:
            detail = "Folder not found" if exc.args[0] == folder_id else "Parent folder not found"
            raise HTTPException(status_code=404, detail=detail) from None
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    app.include_router(router)
