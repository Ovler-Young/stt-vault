from fastapi import APIRouter, FastAPI, HTTPException

from ..auth import admin_password_matches, issue_access_token
from ..requests import LoginRequest
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
            "auth_required": True,
            "transcribe_model": settings.openai_transcribe_model,
            "senko_device": settings.senko_device,
            "batched_embeddings_requested": settings.senko_batched_embeddings,
        }

    @router.post("/api/auth/token")
    def issue_token(payload: LoginRequest) -> dict[str, object]:
        if not admin_password_matches(payload.password, settings.admin_password):
            raise HTTPException(status_code=401, detail="Invalid login credentials")
        return {
            "access_token": issue_access_token(settings),
            "token_type": "bearer",
            "expires_in": max(1, settings.jwt_access_token_minutes) * 60,
        }

    app.include_router(router)
