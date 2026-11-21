"""
A fake calendar that keeps bookings in a Python list.

Everything is lost when the server restarts. That is fine for testing.
"""

from datetime import datetime

from app.calendars.base import Booking, CalendarBackend


class InMemoryCalendar(CalendarBackend):
    def __init__(self) -> None:
        self._bookings: list[Booking] = []

    def list_bookings(self, start: datetime | None = None, end: datetime | None = None) -> list[Booking]:
        result = []
        for booking in self._bookings:
            # Two time ranges overlap when each one starts before the other ends.
            if start is not None and booking.end <= start:
                continue
            if end is not None and booking.start >= end:
                continue
            result.append(booking)
        return sorted(result, key=lambda b: b.start)

    def add_booking(self, booking: Booking) -> None:
        self._bookings.append(booking)

    def remove_booking(self, booking_id: str) -> None:
        self._bookings = [b for b in self._bookings if b.id != booking_id]
