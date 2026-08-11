from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from stt_vault.core.models.mod_contracts import EmbeddingSpaceV1

_SENKO_EMBEDDING_MODEL_SHA256 = "92f29b94e6948786a26778c9e302525d185bb08c8b9f5252ed98776902840199"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    stt_data_dir: Path = Field(default=Path("/data"), alias="STT_DATA_DIR")
    stt_db_path: Path = Field(default=Path("/data/app.sqlite3"), alias="STT_DB_PATH")

    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str = Field(default="https://api.openai.com/v1", alias="OPENAI_BASE_URL")
    openai_transcribe_model: str = Field(
        default="gpt-4o-transcribe",
        alias="OPENAI_TRANSCRIBE_MODEL",
    )
    openai_transcribe_prompt: str = Field(default="", alias="OPENAI_TRANSCRIBE_PROMPT")
    openai_summary_model: str = Field(default="gpt-4o-mini", alias="OPENAI_SUMMARY_MODEL")
    stt_auto_summary_enabled: bool = Field(default=True, alias="STT_AUTO_SUMMARY_ENABLED")
    openai_speaker_name_confidence: float = Field(
        default=0.95,
        ge=0.0,
        le=1.0,
        alias="OPENAI_SPEAKER_NAME_CONFIDENCE",
    )
    openai_concurrency: int = Field(default=2, alias="OPENAI_CONCURRENCY")
    openai_retry_seconds: int = Field(default=60, alias="OPENAI_RETRY_SECONDS")
    openai_max_retries: int = Field(default=5, alias="OPENAI_MAX_RETRIES")
    openai_retry_backoff_seconds: str = Field(
        default="60,300",
        alias="OPENAI_RETRY_BACKOFF_SECONDS",
    )

    stt_transcription_provider: Literal["openai", "mod-whisper-cpu"] = Field(
        alias="STT_TRANSCRIPTION_PROVIDER"
    )
    stt_diarization_provider: Literal["senko"] = Field(alias="STT_DIARIZATION_PROVIDER")
    mod_whisper_cpu_image_digest: str = Field(default="", alias="STT_MOD_WHISPER_CPU_DIGEST")

    diarization_concurrency: int = Field(default=1, alias="DIARIZATION_CONCURRENCY")
    job_lease_seconds: int = Field(default=120, alias="JOB_LEASE_SECONDS")
    diarizer_idle_timeout_seconds: int = Field(default=900, alias="DIARIZER_IDLE_TIMEOUT_SECONDS")
    senko_device: str = Field(default="auto", alias="SENKO_DEVICE")
    senko_embedding_space_id: str = Field(
        default="senko-campplus-192-cosine",
        alias="SENKO_EMBEDDING_SPACE_ID",
    )
    senko_embedding_model_id: str = Field(
        default="speech-campplus-sv-zh-en-16k-common-advanced",
        alias="SENKO_EMBEDDING_MODEL_ID",
    )
    senko_embedding_revision: str = Field(
        default="ba0e12ed923ff49e8c2d9d9a3e42d7923cb95724",
        alias="SENKO_EMBEDDING_REVISION",
    )
    senko_embedding_sha256: str = Field(
        default=_SENKO_EMBEDDING_MODEL_SHA256,
        alias="SENKO_EMBEDDING_SHA256",
    )
    senko_embedding_dimension: int = Field(default=192, ge=1, alias="SENKO_EMBEDDING_DIMENSION")

    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8080, alias="APP_PORT")
    admin_password: str = Field(default="", alias="ADMIN_PASSWORD")
    jwt_secret: str = Field(default="", alias="JWT_SECRET")
    jwt_issuer: str = Field(default="stt-vault", alias="JWT_ISSUER")
    jwt_audience: str = Field(default="stt-vault-api", alias="JWT_AUDIENCE")
    jwt_access_token_minutes: int = Field(default=0, ge=0, alias="JWT_ACCESS_TOKEN_MINUTES")

    max_upload_mb: int = Field(default=4096, alias="MAX_UPLOAD_MB")
    transcribe_chunk_seconds: float = Field(default=60.0, alias="TRANSCRIBE_CHUNK_SECONDS")
    speaker_similarity_threshold: float = Field(
        default=0.875,
        alias="SPEAKER_SIMILARITY_THRESHOLD",
    )
    visual_sample_interval_seconds: float = Field(
        default=2.0,
        alias="VISUAL_SAMPLE_INTERVAL_SECONDS",
    )
    visual_change_threshold: float = Field(default=18.0, alias="VISUAL_CHANGE_THRESHOLD")
    visual_min_gap_seconds: float = Field(default=6.0, alias="VISUAL_MIN_GAP_SECONDS")
    export_formats: str = Field(
        default="json,whisper_json,ai_text,srt,vtt,hyperaudio_html,rttm",
        alias="EXPORT_FORMATS",
    )

    @model_validator(mode="after")
    def validate_selected_mod_digest(self) -> "Settings":
        if self.stt_transcription_provider != "mod-whisper-cpu":
            return self
        digest = self.mod_whisper_cpu_image_digest
        if (
            len(digest) != 71
            or not digest.startswith("sha256:")
            or any(character not in "0123456789abcdef" for character in digest[7:])
        ):
            raise ValueError("STT_MOD_WHISPER_CPU_DIGEST must be a sha256 image digest")
        return self

    @property
    def media_dir(self) -> Path:
        return self.stt_data_dir / "media"

    @property
    def exports_dir(self) -> Path:
        return self.stt_data_dir / "exports"

    @property
    def tmp_dir(self) -> Path:
        return self.stt_data_dir / "tmp"

    @property
    def uploads_dir(self) -> Path:
        return self.stt_data_dir / "uploads"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def parsed_export_formats(self) -> list[str]:
        return [item.strip() for item in self.export_formats.split(",") if item.strip()]

    @property
    def parsed_openai_retry_backoff_seconds(self) -> list[int]:
        values = []
        for item in self.openai_retry_backoff_seconds.split(","):
            item = item.strip()
            if item:
                values.append(max(1, int(item)))
        return values or [self.openai_retry_seconds]

    @property
    def senko_embedding_space(self) -> EmbeddingSpaceV1:
        return EmbeddingSpaceV1(
            space_id=self.senko_embedding_space_id,
            model_id=self.senko_embedding_model_id,
            revision=self.senko_embedding_revision,
            sha256=self.senko_embedding_sha256,
            dimension=self.senko_embedding_dimension,
            metric="cosine",
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
