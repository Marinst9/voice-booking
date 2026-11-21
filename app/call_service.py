"""
The call history rules: saving finished calls and answering questions about them.

Like booking_service.py, it doesn't know which storage is used underneath, and it
doesn't know about Vapi's JSON format either (app/integrations/vapi.py translates that).
"""

import logging
from dataclasses import replace
from datetime import datetime

from app.calendars import CalendarBackend
from app.calls import CallRecord, CallStore
from app.logging_config import log_event

logger = logging.getLogger("app.calls")


class CallService:
    def __init__(self, store: CallStore, calendar: CalendarBackend) -> None:
        self.store = store
        self.calendar = calendar

    def record_call(self, call: CallRecord) -> CallRecord:
        """
        Save a finished call, linked to the booking made during it (if any).

        The booking was saved earlier, while the call was still running: the Vapi adapter
        stored the call id on it. If the caller booked more than once (e.g. booked, then
        changed their mind and booked another time), the latest booking is linked.
        """
        bookings = self.calendar.bookings_for_call(call.id)
        booking_id = bookings[-1].id if bookings else call.booking_id
        saved = self.store.save_call(replace(call, booking_id=booking_id))
        log_event(
            logger,
            logging.INFO,
            "call_recorded",
            vapi_call_id=saved.id,
            duration_seconds=saved.duration_seconds,
            ended_reason=saved.ended_reason,
            booking_id=saved.booking_id,
        )
        return saved

    def get_call(self, call_id: str) -> CallRecord | None:
        return self.store.get_call(call_id)

    def recent_calls(self, limit: int = 50, offset: int = 0) -> list[CallRecord]:
        return self.store.list_calls(limit=limit, offset=offset)

    def call_counts(self, start: datetime, end: datetime) -> tuple[int, int]:
        """(all calls, calls that led to a booking) that started in [start, end)."""
        return self.store.count_calls(start, end)

    def close(self) -> None:
        self.store.close()
