"""
API key check.

Every endpoint (except the /docs page itself) requires the header
    X-API-Key: <the value of API_KEY in your .env>

Using FastAPI's `APIKeyHeader` also adds an "Authorize" button to /docs
where you can paste the key once and then try all endpoints.
"""

import logging
import secrets

from fastapi import Depends, Request, Security
from fastapi.security import APIKeyHeader

from app.config import Settings, get_settings
from app.errors import AppError
from app.logging_config import log_event

logger = logging.getLogger("app.security")

api_key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,  # we raise our own friendly error below instead of FastAPI's default
    description="The API key from your .env file (API_KEY).",
)


def require_api_key(
    request: Request,
    api_key: str | None = Security(api_key_header),
    settings: Settings = Depends(get_settings),
) -> None:
    expected = settings.api_key.get_secret_value() if settings.api_key else None
    # compare_digest takes the same time whether the first or the last character is wrong,
    # so an attacker can't guess the key one character at a time by measuring response times.
    if not expected or not api_key or not secrets.compare_digest(api_key.encode(), expected.encode()):
        log_event(logger, logging.WARNING, "auth_failed", path=request.url.path, key_sent=api_key is not None)
        raise AppError(
            "invalid_api_key",
            "Missing or invalid API key. Send it in the X-API-Key header.",
            status_code=401,
        )
