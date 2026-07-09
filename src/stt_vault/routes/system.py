from fastapi import APIRouter, FastAPI

from ..settings import Settings

__all__ = ["register_system_routes"]


def register_system_routes(app: FastAPI, settings: Settings) -> None:
    router = APIRouter()

    @router.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/api/config")
    def config() -> dict[str, object]:
        return {
            "auth_required": bool(settings.admin_password),
            "transcribe_model": settings.openai_transcribe_model,
            "senko_device": settings.senko_device,
            "batched_embeddings_requested": settings.senko_batched_embeddings,
        }

    app.include_router(router)
