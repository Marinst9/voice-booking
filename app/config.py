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
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ----- Shop -----
    shop_timezone: str = "Europe/Skopje"
    open_days: str = "mon,tue,wed,thu,fri,sat"  # comma-separated, closed on the rest
    open_time: time = time(9, 0)
    close_time: time = time(18, 0)
    slot_step_minutes: int = Field(30, gt=0)  # 30 -> offer 09:00, 09:30, 10:00, ...
    services: list[Service] = DEFAULT_SERVICES  # override with JSON in the SERVICES variable

    # ----- Logging -----
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "text"] = "json"  # json for servers, text is easier to read locally

    # ----- Security -----
    # No default on purpose: the server refuses to start without one (see main.py).
    # SecretStr hides the value if settings are ever printed or logged.
    api_key: SecretStr | None = Field(None, min_length=16)

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

    # ----- Handy derived values -----

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
