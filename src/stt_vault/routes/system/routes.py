from fastapi import APIRouter, FastAPI, HTTPException

from stt_vault.core.auth import admin_password_matches, issue_access_token
from stt_vault.core.config import Settings
from stt_vault.core.models.api import AuthTokenResponse, ConfigResponse
from stt_vault.core.models.requests import LoginRequest

__all__ = ["register_system_routes"]


def register_system_routes(app: FastAPI, settings: Settings) -> None:
    router = APIRouter()

    @router.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/api/config", response_model=ConfigResponse)
    def config() -> ConfigResponse:
        return ConfigResponse(
            auth_required=True,
            transcribe_model=settings.openai_transcribe_model,
            senko_device=settings.senko_device,
            batched_embeddings_requested=settings.senko_batched_embeddings,
        )

    @router.post("/api/auth/token", response_model=AuthTokenResponse)
    def issue_token(payload: LoginRequest) -> AuthTokenResponse:
        if not admin_password_matches(payload.password, settings.admin_password):
            raise HTTPException(status_code=401, detail="Invalid login credentials")
        return AuthTokenResponse(
            access_token=issue_access_token(settings),
            token_type="bearer",
            expires_in=settings.jwt_access_token_minutes * 60
            if settings.jwt_access_token_minutes > 0
            else None,
        )

    app.include_router(router)
