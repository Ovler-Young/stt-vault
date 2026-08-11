from collections.abc import Callable
from dataclasses import dataclass

import uvicorn
from fastapi import FastAPI

from stt_vault.persistence.sqlite_database import SqliteDatabase
from stt_vault.routes import register_api_routes
from stt_vault.workers.worker import Worker

from ..auth import admin_password_matches, require_admin
from ..config import Settings, get_settings
from ..diagnostics.logging import configure_logging
from ..models.requests import SpeakerMergeRequest, SpeakerNameRequest
from .static_frontend import mount_static_frontend

__all__ = [
    "ApplicationDependencies",
    "SpeakerMergeRequest",
    "SpeakerNameRequest",
    "admin_password_matches",
    "create_app",
    "require_admin",
    "run",
]


@dataclass(frozen=True)
class ApplicationDependencies:
    configure_logging: Callable[[], None] = configure_logging
    get_settings: Callable[[], Settings] = get_settings
    prepare_directories: Callable[[Settings], None] | None = None
    database_factory: Callable[[Settings], SqliteDatabase] = lambda settings: SqliteDatabase(
        settings.stt_db_path
    )
    worker_factory: Callable[[Settings, SqliteDatabase], Worker] = Worker
    register_routes: Callable[[FastAPI, Settings, Worker, SqliteDatabase], None] = (
        register_api_routes
    )
    mount_frontend: Callable[[FastAPI], None] = mount_static_frontend
    validate_selected_mod: Callable[[Settings], None] | None = None

    def __post_init__(self) -> None:
        if self.prepare_directories is None:
            object.__setattr__(self, "prepare_directories", prepare_application_directories)
        if self.validate_selected_mod is None:
            object.__setattr__(self, "validate_selected_mod", validate_selected_mod)


def prepare_application_directories(settings: Settings) -> None:
    for path in (
        settings.stt_data_dir,
        settings.media_dir,
        settings.exports_dir,
        settings.tmp_dir,
        settings.uploads_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)


def validate_selected_mod(settings: Settings) -> None:
    if settings.stt_transcription_provider != "mod-whisper-cpu":
        return
    from stt_vault.processing.transcription import SidecarTranscriptionClient

    SidecarTranscriptionClient().validate_startup(
        expected_id="mod-whisper-cpu", expected_digest=settings.mod_whisper_cpu_image_digest
    )


def create_app(dependencies: ApplicationDependencies | None = None) -> FastAPI:
    dependencies = dependencies or ApplicationDependencies()
    dependencies.configure_logging()
    settings = dependencies.get_settings()
    dependencies.prepare_directories(settings)
    database = dependencies.database_factory(settings)
    database.initialize()

    app = FastAPI(title="STT Vault")
    worker = dependencies.worker_factory(settings, database=database)

    @app.on_event("startup")
    def on_startup() -> None:
        dependencies.validate_selected_mod(settings)
        worker.recover_startup_jobs()
        worker.start()

    @app.on_event("shutdown")
    def on_shutdown() -> None:
        worker.stop()
        database.close()

    dependencies.register_routes(app, settings, worker, database)
    dependencies.mount_frontend(app)
    return app


def run() -> None:
    configure_logging()
    settings = get_settings()
    uvicorn.run(
        "stt_vault.core.app:create_app",
        factory=True,
        host=settings.app_host,
        port=settings.app_port,
        log_config=None,
    )


if __name__ == "__main__":
    run()
