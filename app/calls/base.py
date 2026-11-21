"""
The call history "contract", built the same way as app/calendars/base.py.

A `CallRecord` is one finished phone/web call with the voice assistant, saved from
Vapi's end-of-call report. Storage is swappable: in-memory (tests, quick experiments)
or PostgreSQL. Which one is used follows the CALENDAR_BACKEND setting.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass
class CallRecord:
    id: str  # Vapi's call id
    started_at: datetime | None = None  # timezone-aware
    ended_at: datetime | None = None
    duration_seconds: int | None = None
    ended_reason: str | None = None  # e.g. "customer-ended-call", "assistant-ended-call", "silence-timed-out"
    transcript: str | None = None  # "AI: ...\nUser: ..." as Vapi writes it
    summary: str | None = None  # Vapi's AI-written summary of the call
    cost: Decimal | None = None  # in USD
    customer_number: str | None = None  # the caller's phone number (None for browser test calls)
    booking_id: str | None = None  # the booking made during the call, if any
    created_at: datetime | None = None  # when we received the report; filled in by the store

    @property
    def sort_time(self) -> datetime | None:
        """Newest-first lists sort by when the call started (or when we heard about it)."""
        return self.started_at or self.created_at


class CallStore(ABC):
    """Anything that can store the call history."""

    @abstractmethod
    def save_call(self, call: CallRecord) -> CallRecord:
        """
        Insert the call, or update it if a call with the same id is already stored
        (Vapi may send a report again when it retries), and return it as stored.
        """

    @abstractmethod
    def get_call(self, call_id: str) -> CallRecord | None:
        """One call by its Vapi id, or None."""

    @abstractmethod
    def list_calls(self, limit: int = 50, offset: int = 0) -> list[CallRecord]:
        """The newest calls first."""

    @abstractmethod
    def count_calls(self, start: datetime, end: datetime) -> tuple[int, int]:
        """(all calls, calls that led to a booking) for calls that started in [start, end)."""

    def close(self) -> None:  # noqa: B027  (optional hook)
        """Release resources such as database connections when the app shuts down."""
