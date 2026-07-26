import uvicorn
from fastapi import FastAPI

from . import db
from .auth import admin_password_matches, require_admin
from .logging_config import configure_logging
from .requests import SpeakerMergeRequest, SpeakerNameRequest
from .routes import register_api_routes
from .settings import get_settings
from .static_frontend import mount_static_frontend
from .worker import Worker

__all__ = [
    "SpeakerMergeRequest",
    "SpeakerNameRequest",
    "admin_password_matches",
    "create_app",
    "require_admin",
    "run",
]


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    settings.stt_data_dir.mkdir(parents=True, exist_ok=True)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    settings.exports_dir.mkdir(parents=True, exist_ok=True)
    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    db.initialize(settings.stt_db_path)
    db.recover_expired_jobs(settings.stt_db_path)

    app = FastAPI(title="STT Vault")
    worker = Worker(settings)

    @app.on_event("startup")
    def on_startup() -> None:
        worker.start()

    @app.on_event("shutdown")
    def on_shutdown() -> None:
        worker.stop()

    register_api_routes(app, settings, worker)
    mount_static_frontend(app)
    return app


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "stt_vault.app:create_app",
        factory=True,
        host=settings.app_host,
        port=settings.app_port,
    )


if __name__ == "__main__":
    run()
