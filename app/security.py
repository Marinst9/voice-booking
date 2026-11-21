"""
Request authentication.

1. Booking endpoints: the header `X-API-Key: <API_KEY from .env>`.
   Using FastAPI's `APIKeyHeader` also adds an "Authorize" button to /docs.

2. The Vapi webhook (/vapi/tools): the secret Vapi sends from a "Custom Credential"
   (Bearer Token type) in your Vapi dashboard, set to the same value as VAPI_SECRET.
   Vapi sends it either as
       Authorization: Bearer <secret>      (default)
       X-Vapi-Secret: <secret>             (legacy style, header name X-Vapi-Secret, no Bearer prefix)
   and both are accepted.
"""

import logging
import secrets

from fastapi import Depends, Request, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from pydantic import SecretStr

from app.config import Settings, get_settings
from app.errors import AppError
from app.logging_config import log_event

logger = logging.getLogger("app.security")


def secret_matches(sent: str | None, expected: SecretStr | None) -> bool:
    """
    True only if a secret is configured AND the sent value equals it.

    compare_digest takes the same time whether the first or the last character is wrong,
    so an attacker can't guess the secret one character at a time by measuring response times.
    """
    if not expected or not sent:
        return False
    return secrets.compare_digest(sent.encode(), expected.get_secret_value().encode())


# ---------- 1. X-API-Key for the booking endpoints ----------

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
    if not secret_matches(api_key, settings.api_key):
        log_event(logger, logging.WARNING, "auth_failed", path=request.url.path, key_sent=api_key is not None)
        raise AppError(
            "invalid_api_key",
            "Missing or invalid API key. Send it in the X-API-Key header.",
            status_code=401,
        )


# ---------- 2. Vapi credential for /vapi/tools ----------

vapi_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="VapiBearer",
    description="VAPI_SECRET, sent by a Vapi Bearer Token credential as `Authorization: Bearer ...`.",
)
vapi_secret_header = APIKeyHeader(
    name="X-Vapi-Secret",
    auto_error=False,
    scheme_name="VapiSecretHeader",
    description="VAPI_SECRET, legacy style: sent as the X-Vapi-Secret header.",
)


def require_vapi_secret(
    request: Request,
    bearer: HTTPAuthorizationCredentials | None = Security(vapi_bearer),
    legacy_secret: str | None = Security(vapi_secret_header),
    settings: Settings = Depends(get_settings),
) -> None:
    token = bearer.credentials if bearer else None
    if not (secret_matches(token, settings.vapi_secret) or secret_matches(legacy_secret, settings.vapi_secret)):
        log_event(
            logger,
            logging.WARNING,
            "auth_failed",
            path=request.url.path,
            key_sent=token is not None or legacy_secret is not None,
            vapi_secret_configured=settings.vapi_secret is not None,
        )
        raise AppError(
            "invalid_vapi_secret",
            "Missing or invalid Vapi credential.",
            status_code=401,
        )
