"""
Login for the admin dashboard.

How it works:
- The username and password come from .env (ADMIN_USERNAME, ADMIN_PASSWORD).
- After a correct login we put {"admin": "<username>"} in the *session*: a cookie that
  Starlette's SessionMiddleware signs with SESSION_SECRET. The browser can read it but
  can't change it, because the signature would no longer match. So it holds no secrets.
- Every admin page depends on `require_admin`. Without a logged-in session it sends
  the browser to the login page.

CSRF protection: a form on another website could try to make your logged-in browser
POST to /admin/bookings/.../cancel. To stop that, every form we render carries a random
token that is also stored in the session, and every POST checks that the two match.
The cookie is also SameSite=Lax, so browsers don't send it with POSTs from other sites anyway.
"""

import logging
import secrets

from fastapi import Depends, Request

from app.config import Settings, get_settings
from app.logging_config import log_event
from app.security import secret_matches

logger = logging.getLogger("app.admin")

SESSION_USER = "admin"
SESSION_CSRF = "csrf"


class AdminLoginRequired(Exception):
    """Raised by `require_admin`; main.py turns it into a redirect to the login page."""

    def __init__(self, next_path: str) -> None:
        self.next_path = next_path


class CsrfError(Exception):
    """A POST without the right form token. main.py answers 403."""


def csrf_token(request: Request) -> str:
    """The session's form token (made on first use). Templates put it in a hidden field."""
    token = request.session.get(SESSION_CSRF)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[SESSION_CSRF] = token
    return token


def check_csrf(request: Request, sent: str | None) -> None:
    expected = request.session.get(SESSION_CSRF)
    if not expected or not sent or not secrets.compare_digest(sent, expected):
        log_event(logger, logging.WARNING, "csrf_rejected", path=request.url.path)
        raise CsrfError()


def check_credentials(username: str, password: str, settings: Settings) -> bool:
    if settings.admin_password is None:
        return False
    # Check both, always: stopping early when the username is wrong would make that case
    # measurably faster, telling an attacker which usernames exist.
    username_ok = secrets.compare_digest(username.encode(), settings.admin_username.encode())
    password_ok = secret_matches(password, settings.admin_password)
    return username_ok and password_ok


def log_in(request: Request, username: str) -> None:
    # Start a fresh session, so nothing from before the login carries over.
    request.session.clear()
    request.session[SESSION_USER] = username
    csrf_token(request)
    log_event(logger, logging.INFO, "admin_login", username=username)


def log_out(request: Request) -> None:
    username = request.session.get(SESSION_USER)
    request.session.clear()
    log_event(logger, logging.INFO, "admin_logout", username=username)


def current_admin(request: Request, settings: Settings) -> str | None:
    """The logged-in username, or None. A session for a different username (changed in .env) is ignored."""
    username = request.session.get(SESSION_USER)
    if settings.admin_password is None or username != settings.admin_username:
        return None
    return username


def require_admin(request: Request, settings: Settings = Depends(get_settings)) -> str:
    username = current_admin(request, settings)
    if username is None:
        raise AdminLoginRequired(next_path=request.url.path)
    return username


def safe_next_path(next_path: str | None) -> str:
    """Only go back to our own admin pages after login (never to another website)."""
    if next_path and next_path.startswith("/admin") and not next_path.startswith("//"):
        return next_path
    return "/admin"
