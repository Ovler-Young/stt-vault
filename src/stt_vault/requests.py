from pydantic import BaseModel

__all__ = ["LoginRequest", "SpeakerMergeRequest", "SpeakerNameRequest"]


class LoginRequest(BaseModel):
    password: str


class SpeakerNameRequest(BaseModel):
    display_name: str


class SpeakerMergeRequest(BaseModel):
    source_speaker_id: str
