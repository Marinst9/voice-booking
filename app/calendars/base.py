"""
The calendar "contract".

The booking rules (booking_service.py) only talk to a calendar through
these methods. That means the fake calendar can be swapped for Google
Calendar by writing one new class with the same methods, without
touching the endpoints or the booking rules.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Booking:
    id: str
    customer_name: str
    phone: str            # stored in a normalized form (digits and leading +)
    service_key: str
    service_name: str
    start: datetime       # timezone-aware, Europe/Skopje
    end: datetime


class CalendarBackend(ABC):
    """Anything that can store bookings: in-memory, Google Calendar, a database..."""

    @abstractmethod
    def list_bookings(self, start: datetime | None = None, end: datetime | None = None) -> list[Booking]:
        """Return bookings that overlap [start, end), sorted by start time. No limits = all bookings."""

    @abstractmethod
    def add_booking(self, booking: Booking) -> None:
        """Save a new booking."""

    @abstractmethod
    def remove_booking(self, booking_id: str) -> None:
        """Delete a booking by its id."""
