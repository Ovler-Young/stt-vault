from typing import Annotated
from urllib.parse import unquote

from fastapi import Cookie, Depends, Header, HTTPException, Request

from .settings import Settings, get_settings

ADMIN_PASSWORD_COOKIE_NAME = "stt-vault-admin-password"
COOKIE_AUTH_METHODS = {"GET", "HEAD"}

__all__ = [
    "ADMIN_PASSWORD_COOKIE_NAME",
    "COOKIE_AUTH_METHODS",
    "admin_password_matches",
    "require_admin",
]


def require_admin(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    x_stt_admin_password: Annotated[str | None, Header()] = None,
    stt_vault_admin_password: Annotated[
        str | None,
        Cookie(alias=ADMIN_PASSWORD_COOKIE_NAME),
    ] = None,
) -> None:
    if not settings.admin_password:
        return
    if admin_password_matches(x_stt_admin_password, settings.admin_password):
        return
    if request.method in COOKIE_AUTH_METHODS and admin_password_matches(
        stt_vault_admin_password,
        settings.admin_password,
        quoted=True,
    ):
        return
    raise HTTPException(status_code=401, detail="Missing or invalid admin password")


def admin_password_matches(candidate: str | None, expected: str, *, quoted: bool = False) -> bool:
    if candidate is None:
        return False
    if quoted:
        return unquote(candidate) == expected
    return candidate == expected
