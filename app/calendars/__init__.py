"""
Calendar storage.

`CalendarBackend` (base.py) describes WHAT a calendar must be able to do.
- `InMemoryCalendar` (memory.py): fake, keeps bookings in memory. Used by fast tests.
- `PostgresCalendar` (postgres.py): real database storage.
Later you can add e.g. `GoogleCalendar(CalendarBackend)` next to them.

`create_calendar(settings)` picks one based on the CALENDAR_BACKEND setting.
"""

from app.calendars.base import Booking, BookingStatus, CalendarBackend, SlotTakenError
from app.calendars.memory import InMemoryCalendar
from app.config import Settings


def create_calendar(settings: Settings) -> CalendarBackend:
    if settings.calendar_backend == "postgres":
        # Imported here so the in-memory mode doesn't need a database driver loaded.
        from app.calendars.postgres import PostgresCalendar

        return PostgresCalendar(settings.database_url)
    return InMemoryCalendar()


__all__ = [
    "Booking",
    "BookingStatus",
    "CalendarBackend",
    "InMemoryCalendar",
    "SlotTakenError",
    "create_calendar",
]
