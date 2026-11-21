"""
A fake calendar that keeps bookings in a Python list.

Everything is lost when the server restarts. It's used by the fast tests
and for quick local experiments (CALENDAR_BACKEND=memory). It follows
exactly the same rules as the PostgreSQL calendar.
"""

from dataclasses import replace
from datetime import datetime

from app.calendars.base import Booking, BookingStatus, CalendarBackend, SlotTakenError


def _overlaps(booking: Booking, start: datetime, end: datetime) -> bool:
    # Two time ranges overlap when each one starts before the other ends.
    # So 13:30-14:00 and 14:00-14:30 do NOT overlap (back-to-back is fine).
    return booking.start < end and start < booking.end


class InMemoryCalendar(CalendarBackend):
    def __init__(self) -> None:
        self._bookings: list[Booking] = []

    def list_bookings(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        include_cancelled: bool = False,
    ) -> list[Booking]:
        result = [
            b
            for b in self._bookings
            if (include_cancelled or b.status == BookingStatus.CONFIRMED)
            and (start is None or b.end > start)
            and (end is None or b.start < end)
        ]
        return sorted(result, key=lambda b: b.start)

    def add_booking(self, booking: Booking) -> Booking:
        # Same rule the database enforces with its exclusion constraint.
        if any(_overlaps(b, booking.start, booking.end) for b in self.list_bookings()):
            raise SlotTakenError(booking.id)
        stored = replace(booking, status=BookingStatus.CONFIRMED, created_at=datetime.now(booking.start.tzinfo))
        self._bookings.append(stored)
        return stored

    def find_booking(self, phone: str, start: datetime) -> Booking | None:
        for booking in self.list_bookings():
            if booking.phone == phone and booking.start == start:
                return booking
        return None

    def get_booking(self, booking_id: str) -> Booking | None:
        return next((b for b in self._bookings if b.id == booking_id), None)

    def bookings_for_call(self, call_id: str) -> list[Booking]:
        # The list is in the order bookings were added, so it's already oldest first.
        return [b for b in self._bookings if b.call_id == call_id]

    def cancel_booking(self, booking_id: str) -> Booking:
        for index, booking in enumerate(self._bookings):
            if booking.id == booking_id:
                self._bookings[index] = replace(booking, status=BookingStatus.CANCELLED)
                return self._bookings[index]
        raise KeyError(booking_id)
