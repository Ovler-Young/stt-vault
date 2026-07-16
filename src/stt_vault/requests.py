from pydantic import BaseModel, Field

__all__ = [
    "AssetMoveRequest",
    "FolderCreateRequest",
    "FolderMoveRequest",
    "FolderRenameRequest",
    "LoginRequest",
    "SpeakerMergeRequest",
    "SpeakerNameRequest",
    "UploadCreateRequest",
]


class LoginRequest(BaseModel):
    password: str


class FolderCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    parent_id: str | None = None


class FolderMoveRequest(BaseModel):
    parent_id: str | None = None


class FolderRenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class AssetMoveRequest(BaseModel):
    parent_folder_id: str | None = None


class UploadCreateRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=1024)
    size: int = Field(ge=1)


class SpeakerNameRequest(BaseModel):
    display_name: str


class SpeakerMergeRequest(BaseModel):
    source_speaker_id: str
