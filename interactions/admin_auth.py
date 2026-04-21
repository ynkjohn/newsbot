from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from config.settings import settings

_basic_auth = HTTPBasic(auto_error=False)
_AUTH_HEADERS = {"WWW-Authenticate": 'Basic realm="newsbot-admin"'}


def require_admin(
    credentials: Annotated[HTTPBasicCredentials | None, Depends(_basic_auth)],
) -> str:
    if not settings.admin_auth_enabled:
        return "anonymous"

    if not settings.admin_password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin authentication is not configured.",
        )

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers=_AUTH_HEADERS,
        )

    username_ok = secrets.compare_digest(credentials.username, settings.admin_username)
    password_ok = secrets.compare_digest(credentials.password, settings.admin_password)
    if not username_ok or not password_ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials.",
            headers=_AUTH_HEADERS,
        )

    return credentials.username
