from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

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
    openai_concurrency: int = Field(default=2, alias="OPENAI_CONCURRENCY")
    openai_retry_seconds: int = Field(default=60, alias="OPENAI_RETRY_SECONDS")
    openai_max_retries: int = Field(default=5, alias="OPENAI_MAX_RETRIES")
    openai_retry_backoff_seconds: str = Field(
        default="60,300",
        alias="OPENAI_RETRY_BACKOFF_SECONDS",
    )

    diarization_concurrency: int = Field(default=1, alias="DIARIZATION_CONCURRENCY")
    job_lease_seconds: int = Field(default=120, alias="JOB_LEASE_SECONDS")
    diarizer_idle_timeout_seconds: int = Field(default=900, alias="DIARIZER_IDLE_TIMEOUT_SECONDS")
    senko_device: str = Field(default="auto", alias="SENKO_DEVICE")
    senko_batched_embeddings: bool = Field(default=True, alias="SENKO_BATCHED_EMBEDDINGS")
    senko_fbank_batch_segments: int = Field(default=256, alias="SENKO_FBANK_BATCH_SEGMENTS")

    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8080, alias="APP_PORT")
    admin_password: str = Field(default="", alias="ADMIN_PASSWORD")
    jwt_secret: str = Field(default="", alias="JWT_SECRET")
    jwt_issuer: str = Field(default="stt-vault", alias="JWT_ISSUER")
    jwt_audience: str = Field(default="stt-vault-api", alias="JWT_AUDIENCE")
    jwt_access_token_minutes: int = Field(default=60, alias="JWT_ACCESS_TOKEN_MINUTES")

    max_upload_mb: int = Field(default=4096, alias="MAX_UPLOAD_MB")
    transcribe_chunk_seconds: float = Field(default=45.0, alias="TRANSCRIBE_CHUNK_SECONDS")
    transcribe_chunk_overlap_seconds: float = Field(
        default=1.0,
        alias="TRANSCRIBE_CHUNK_OVERLAP_SECONDS",
    )
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
