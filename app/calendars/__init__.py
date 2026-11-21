"""
Calendar storage.

`CalendarBackend` describes WHAT a calendar must be able to do.
`InMemoryCalendar` is a fake one that keeps bookings in memory.
Later you can add e.g. `GoogleCalendar(CalendarBackend)` next to it.
"""

from app.calendars.base import Booking, CalendarBackend
from app.calendars.memory import InMemoryCalendar

__all__ = ["Booking", "CalendarBackend", "InMemoryCalendar"]
