"""
Shop settings: timezone, opening hours and services.

If you want to change the opening hours or add a service, this is the
only file you need to edit.
"""

from dataclasses import dataclass
from datetime import time
from zoneinfo import ZoneInfo

# All times in the app are in the shop's local time.
TIMEZONE = ZoneInfo("Europe/Skopje")

# Opening hours per weekday. Python counts weekdays as Monday=0 ... Sunday=6.
# A day that is missing from this dictionary (Sunday) means "closed".
OPENING_HOURS: dict[int, tuple[time, time]] = {
    0: (time(9, 0), time(18, 0)),  # Monday
    1: (time(9, 0), time(18, 0)),  # Tuesday
    2: (time(9, 0), time(18, 0)),  # Wednesday
    3: (time(9, 0), time(18, 0)),  # Thursday
    4: (time(9, 0), time(18, 0)),  # Friday
    5: (time(9, 0), time(18, 0)),  # Saturday
}

# How far apart the start times we offer are (09:00, 09:30, 10:00, ...).
SLOT_STEP_MINUTES = 30


@dataclass(frozen=True)
class Service:
    key: str               # short id used in code, e.g. "haircut"
    name: str              # name spoken to the caller, e.g. "Haircut"
    duration_minutes: int


SERVICES: list[Service] = [
    Service("haircut", "Haircut", 30),
    Service("beard_trim", "Beard trim", 20),
    Service("haircut_beard", "Haircut + Beard", 50),
]


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
