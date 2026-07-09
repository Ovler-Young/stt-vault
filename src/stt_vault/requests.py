from pydantic import BaseModel

__all__ = ["SpeakerMergeRequest", "SpeakerNameRequest"]


class SpeakerNameRequest(BaseModel):
    display_name: str


class SpeakerMergeRequest(BaseModel):
    source_speaker_id: str
