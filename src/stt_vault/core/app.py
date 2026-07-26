from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from stt_vault.persistence import db
from stt_vault.routes import register_api_routes
from stt_vault.workers.worker import Worker

from .auth import admin_password_matches, require_admin
from .logging_config import configure_logging
from .requests import SpeakerMergeRequest, SpeakerNameRequest
from .settings import Settings, get_settings
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
    initialize_database: Callable[[Path], None] = db.initialize
    recover_expired_jobs: Callable[[Path], None] = db.recover_expired_jobs
    worker_factory: Callable[[Settings], Worker] = Worker
    register_routes: Callable[[FastAPI, Settings, Worker], None] = register_api_routes
    mount_frontend: Callable[[FastAPI], None] = mount_static_frontend

    def __post_init__(self) -> None:
        if self.prepare_directories is None:
            object.__setattr__(self, "prepare_directories", prepare_application_directories)


def prepare_application_directories(settings: Settings) -> None:
    for path in (
        settings.stt_data_dir,
        settings.media_dir,
        settings.exports_dir,
        settings.tmp_dir,
        settings.uploads_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)


def create_app(dependencies: ApplicationDependencies | None = None) -> FastAPI:
    dependencies = dependencies or ApplicationDependencies()
    dependencies.configure_logging()
    settings = dependencies.get_settings()
    dependencies.prepare_directories(settings)
    dependencies.initialize_database(settings.stt_db_path)
    dependencies.recover_expired_jobs(settings.stt_db_path)

    app = FastAPI(title="STT Vault")
    worker = dependencies.worker_factory(settings)

    @app.on_event("startup")
    def on_startup() -> None:
        worker.start()

    @app.on_event("shutdown")
    def on_shutdown() -> None:
        worker.stop()

    dependencies.register_routes(app, settings, worker)
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
