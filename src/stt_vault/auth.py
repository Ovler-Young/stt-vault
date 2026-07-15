from datetime import UTC, datetime, timedelta
from secrets import compare_digest
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .settings import Settings, get_settings

bearer_scheme = HTTPBearer(auto_error=False)

__all__ = [
    "admin_password_matches",
    "issue_access_token",
    "require_admin",
]


def require_admin(
    settings: Annotated[Settings, Depends(get_settings)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> None:
    if not settings.jwt_secret:
        raise HTTPException(status_code=503, detail="JWT_SECRET is not configured")
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    try:
        claims = jwt.decode(
            credentials.credentials,
            settings.jwt_secret,
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["aud", "exp", "iat", "iss", "role", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid bearer token") from exc
    if claims.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Administrator token required")


def issue_access_token(settings: Settings) -> str:
    if not settings.jwt_secret:
        raise HTTPException(status_code=503, detail="JWT_SECRET is not configured")
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": "single-user-admin",
            "role": "admin",
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": now,
            "exp": now + timedelta(minutes=max(1, settings.jwt_access_token_minutes)),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )


def admin_password_matches(candidate: str | None, expected: str) -> bool:
    return candidate is not None and bool(expected) and compare_digest(candidate, expected)
