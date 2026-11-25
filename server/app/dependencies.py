"""FastAPI dependency injection providers for authentication and settings."""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Form, Header, HTTPException

from app.config import Settings, get_settings
from app.schemas import DownloadRequest


def get_authenticated_settings(
    settings: Annotated[Settings, Depends(get_settings)],
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Settings:
    """Validate API authentication key if configured and return Settings instance."""
    if settings.api_auth_key and (not x_api_key or not hmac.compare_digest(x_api_key, settings.api_auth_key)):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return settings


def get_download_auth_settings(
    payload: DownloadRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Settings:
    """Validate download request auth via header or payload apiKey."""
    if settings.api_auth_key:
        auth_key = x_api_key or payload.api_key
        if not auth_key or not hmac.compare_digest(auth_key, settings.api_auth_key):
            raise HTTPException(
                status_code=401,
                detail="Unauthorized: invalid or missing API authentication key",
            )
    return settings


def get_stream_auth_settings(
    settings: Annotated[Settings, Depends(get_settings)],
    api_key: str | None = None,
    apiKey: str | None = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Settings:
    """Validate streaming request auth via X-API-Key header or query parameter."""
    if settings.api_auth_key:
        key = x_api_key or apiKey or api_key
        if not key or not hmac.compare_digest(key, settings.api_auth_key):
            raise HTTPException(status_code=401, detail="Unauthorized")
    return settings


def get_upload_auth_settings(
    settings: Annotated[Settings, Depends(get_settings)],
    apiKey: Annotated[str | None, Form()] = None,
    api_key: Annotated[str | None, Form()] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Settings:
    """Validate upload request auth via X-API-Key header or form field."""
    if settings.api_auth_key:
        auth_key = x_api_key or apiKey or api_key
        if not auth_key or not hmac.compare_digest(auth_key, settings.api_auth_key):
            raise HTTPException(
                status_code=401,
                detail="Unauthorized: invalid or missing API authentication key",
            )
    return settings


