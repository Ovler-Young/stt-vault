from pydantic import BaseModel, Field

__all__ = [
    "AssetMoveRequest",
    "FolderCreateRequest",
    "FolderMoveRequest",
    "LoginRequest",
    "SpeakerMergeRequest",
    "SpeakerNameRequest",
]


class LoginRequest(BaseModel):
    password: str


class FolderCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    parent_id: str | None = None


class FolderMoveRequest(BaseModel):
    parent_id: str | None = None


class AssetMoveRequest(BaseModel):
    parent_folder_id: str | None = None


class SpeakerNameRequest(BaseModel):
    display_name: str


class SpeakerMergeRequest(BaseModel):
    source_speaker_id: str
