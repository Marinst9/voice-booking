"""
The calendar "contract".

The booking rules (booking_service.py) only talk to a calendar through
these methods. That means the storage can be swapped (in-memory, PostgreSQL,
later Google Calendar) by writing one new class with the same methods,
without touching the endpoints or the booking rules.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class BookingStatus(StrEnum):
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


@dataclass
class Booking:
    id: str
    customer_name: str
    phone: str  # stored in a normalized form (digits and leading +)
    service_key: str
    service_name: str
    start: datetime  # timezone-aware, Europe/Skopje
    end: datetime
    status: BookingStatus = BookingStatus.CONFIRMED
    created_at: datetime | None = None  # filled in by the calendar when saved


class SlotTakenError(Exception):
    """Raised by `add_booking` when the time overlaps another confirmed booking."""


class CalendarBackend(ABC):
    """Anything that can store bookings: in-memory, PostgreSQL, Google Calendar..."""

    @abstractmethod
    def list_bookings(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        include_cancelled: bool = False,
    ) -> list[Booking]:
        """Bookings that overlap [start, end), sorted by start time. No limits = all bookings."""

    @abstractmethod
    def add_booking(self, booking: Booking) -> Booking:
        """
        Save a new confirmed booking and return it as stored (with `created_at`).

        Must raise SlotTakenError if it overlaps another confirmed booking. This is the
        last line of defence against double bookings, even if two requests arrive at once.
        """

    @abstractmethod
    def find_booking(self, phone: str, start: datetime) -> Booking | None:
        """The confirmed booking with this (normalized) phone and start time, or None."""

    @abstractmethod
    def cancel_booking(self, booking_id: str) -> Booking:
        """Mark a booking as cancelled (it is kept for history) and return it."""

    def close(self) -> None:  # noqa: B027  (optional hook, most calendars need nothing)
        """Release resources such as database connections when the app shuts down."""
