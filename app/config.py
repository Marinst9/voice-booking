"""
App settings, read from environment variables (or a `.env` file).

Every setting has a sensible default for the barber shop, so the app runs
without any configuration. To change one, set an environment variable with
the same name in CAPITALS, e.g. `CLOSE_TIME=19:00`. See `.env.example`.

The rest of the app imports the ready-made values at the bottom of this
file (TIMEZONE, OPENING_HOURS, SERVICES, ...).
"""

from datetime import time
from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

WEEKDAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class Service(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str  # short id used in code, e.g. "haircut"
    name: str  # name spoken to the caller, e.g. "Haircut"
    duration_minutes: int = Field(gt=0)


DEFAULT_SERVICES = [
    Service(key="haircut", name="Haircut", duration_minutes=30),
    Service(key="beard_trim", name="Beard trim", duration_minutes=20),
    Service(key="haircut_beard", name="Haircut + Beard", duration_minutes=50),
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # `VAPI_SECRET=` (empty) in .env means "not set", not "set to an empty string".
        env_ignore_empty=True,
    )

    # ----- Shop -----
    shop_timezone: str = "Europe/Skopje"
    open_days: str = "mon,tue,wed,thu,fri,sat"  # comma-separated, closed on the rest
    open_time: time = time(9, 0)
    close_time: time = time(18, 0)
    slot_step_minutes: int = Field(30, gt=0)  # 30 -> offer 09:00, 09:30, 10:00, ...
    services: list[Service] = DEFAULT_SERVICES  # override with JSON in the SERVICES variable

    # ----- Where the app runs -----
    # "production" switches on the safe defaults (see the checks below and README "Production settings").
    environment: Literal["development", "production"] = "development"
    # The interactive docs at /docs, /redoc and /openapi.json. Empty = on in development, off in production.
    docs_enabled: bool | None = None

    # ----- Storage -----
    # "memory" = fake calendar (lost on restart), "postgres" = real database.
    calendar_backend: Literal["memory", "postgres"] = "memory"
    # e.g. postgresql+psycopg://booking:booking@127.0.0.1:5432/booking
    # Hosting platforms give "postgresql://..." or "postgres://..."; both are accepted (see below).
    database_url: str | None = None

    # ----- Rate limiting (per client IP; 0 = no limit) -----
    rate_limit_api_per_minute: int = Field(60, ge=0)  # REST endpoints
    rate_limit_vapi_per_minute: int = Field(300, ge=0)  # /vapi/... (all of Vapi's calls come from few IPs)
    login_max_failures: int = Field(5, ge=0)  # wrong admin logins allowed per window...
    login_lockout_minutes: int = Field(15, gt=0)  # ...before that IP has to wait this long
    # Behind a hosting platform's proxy, every request seems to come from the proxy. This names the
    # header in which the proxy passes the real client IP (Railway: X-Real-IP). Empty = use the
    # connection's address (right for local runs and Docker Compose). Only set it when a proxy you
    # trust always sets this header, otherwise clients could fake their IP to dodge the limits.
    client_ip_header: str | None = None

    # ----- Logging -----
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "text"] = "json"  # json for servers, text is easier to read locally

    # ----- Security -----
    # No default on purpose: the server refuses to start without one (see main.py).
    # SecretStr hides the value if settings are ever printed or logged.
    api_key: SecretStr | None = Field(None, min_length=16)
    # The token Vapi sends with every tool call (set the same value in a Vapi "Bearer Token" credential).
    # Optional: without it, /vapi/tools rejects every request.
    vapi_secret: SecretStr | None = Field(None, min_length=16)

    # ----- Admin dashboard (/admin) -----
    admin_username: str = Field("admin", min_length=1)
    # No password = the dashboard login is turned off.
    admin_password: SecretStr | None = Field(None, min_length=10)
    # Signs the login cookie so nobody can forge one. Without it, a random one is made at every start
    # (works, but everyone is logged out when the server restarts).
    session_secret: SecretStr | None = Field(None, min_length=32)
    # True = the browser only sends the login cookie over HTTPS.
    # Empty = off in development (plain http://127.0.0.1), on in production (which must be HTTPS).
    session_cookie_secure: bool | None = None
    session_max_age_hours: int = Field(8, gt=0)  # log in again after this long

    # ----- Checks: fail at startup with a clear message instead of misbehaving later -----

    @field_validator("shop_timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except Exception as exc:
            raise ValueError(f"unknown timezone '{value}'") from exc
        return value

    @field_validator("open_days")
    @classmethod
    def _valid_days(cls, value: str) -> str:
        days = [d.strip().lower() for d in value.split(",") if d.strip()]
        unknown = [d for d in days if d not in WEEKDAY_NAMES]
        if not days or unknown:
            raise ValueError(f"use day names like mon,tue,...; got {value!r}")
        return ",".join(days)

    @model_validator(mode="after")
    def _close_after_open(self) -> "Settings":
        if self.close_time <= self.open_time:
            raise ValueError("CLOSE_TIME must be later than OPEN_TIME")
        return self

    @model_validator(mode="after")
    def _postgres_needs_url(self) -> "Settings":
        if self.calendar_backend == "postgres" and not self.database_url:
            raise ValueError("CALENDAR_BACKEND=postgres needs DATABASE_URL")
        return self

    @field_validator("database_url")
    @classmethod
    def _use_psycopg_driver(cls, value: str | None) -> str | None:
        """'postgresql://...' or 'postgres://...' (as Railway, Render, Heroku write it) -> 'postgresql+psycopg://...'"""
        if value is None:
            return None
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value.removeprefix(prefix)
        return value

    @model_validator(mode="after")
    def _safe_production(self) -> "Settings":
        """In production, refuse to start with a setting that would lose data or weaken security."""
        if self.environment != "production":
            return self
        problems = []
        if self.api_key is None:
            problems.append("API_KEY must be set")
        if self.calendar_backend != "postgres":
            problems.append("CALENDAR_BACKEND must be postgres (the memory calendar loses everything on restart)")
        if self.admin_password is not None and self.session_secret is None:
            problems.append("SESSION_SECRET must be set when ADMIN_PASSWORD is set")
        if self.session_cookie_secure is False:
            problems.append("SESSION_COOKIE_SECURE can't be false (production runs on HTTPS)")
        if problems:
            raise ValueError("ENVIRONMENT=production, but: " + "; ".join(problems))
        return self

    # ----- Handy derived values -----

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def show_docs(self) -> bool:
        return self.docs_enabled if self.docs_enabled is not None else not self.is_production

    @property
    def secure_cookies(self) -> bool:
        return self.session_cookie_secure if self.session_cookie_secure is not None else self.is_production

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.shop_timezone)

    @property
    def opening_hours(self) -> dict[int, tuple[time, time]]:
        """Weekday number (Monday=0) -> (open, close). Missing day = closed."""
        return {WEEKDAY_NAMES.index(day): (self.open_time, self.close_time) for day in self.open_days.split(",")}


@lru_cache
def get_settings() -> Settings:
    """Read the settings once and reuse them."""
    return Settings()


# ---------- Values used across the app ----------

settings = get_settings()
TIMEZONE = settings.timezone
OPENING_HOURS = settings.opening_hours
SLOT_STEP_MINUTES = settings.slot_step_minutes
SERVICES: list[Service] = settings.services


def _simplify(text: str) -> str:
    """Lowercase and keep only letters, so 'Beard Trim' == 'beard_trim' == 'beardtrim'."""
    text = text.lower().replace("+", " and ").replace("&", " and ")
    return "".join(ch for ch in text if ch.isalpha())


def find_service(text: str) -> Service | None:
    """
    Find a service from whatever the voice agent sends.

    Accepts the key ("haircut_beard"), the name ("Haircut + Beard") or
    natural variants ("haircut and beard"). Returns None if nothing matches.
    """
    wanted = _simplify(text)
    for service in SERVICES:
        if wanted in (_simplify(service.key), _simplify(service.name)):
            return service
    return None
