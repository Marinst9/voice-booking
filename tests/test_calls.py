"""
Tests for the call history storage and CallService (no API).

Like the calendar tests, these run against both the in-memory store and PostgreSQL.
"""

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

from app.calendars import Booking
from app.calls import CallRecord
from app.config import TIMEZONE

MONDAY_9 = datetime(2026, 11, 23, 9, 0, tzinfo=TIMEZONE)


def make_call(call_id: str = "call-1", started: datetime = MONDAY_9, **changes) -> CallRecord:
    call = CallRecord(
        id=call_id,
        started_at=started,
        ended_at=started + timedelta(minutes=2, seconds=15),
        duration_seconds=135,
        ended_reason="customer-ended-call",
        transcript="AI: Hello!\nUser: Hi, a haircut please.",
        summary="The caller asked for a haircut.",
        cost=Decimal("0.0634"),
        customer_number="+38970123456",
    )
    return replace(call, **changes)


def make_booking(booking_id: str, call_id: str | None, hour: int) -> Booking:
    start = datetime(2026, 11, 24, hour, 0, tzinfo=TIMEZONE)
    return Booking(
        id=booking_id,
        customer_name="Marko",
        phone="070123456",
        service_key="haircut",
        service_name="Haircut",
        start=start,
        end=start + timedelta(minutes=30),
        call_id=call_id,
    )


# ---------- The store ----------


def test_save_and_get(call_store):
    stored = call_store.save_call(make_call())
    assert stored.created_at is not None

    loaded = call_store.get_call("call-1")
    assert loaded.started_at == MONDAY_9
    assert loaded.started_at.utcoffset() == timedelta(hours=1)  # back in Skopje time
    assert loaded.duration_seconds == 135
    assert loaded.cost == Decimal("0.0634")
    assert loaded.transcript.startswith("AI: Hello!")
    assert call_store.get_call("unknown") is None


def test_saving_again_updates_instead_of_duplicating(call_store):
    first = call_store.save_call(make_call(summary="old"))
    second = call_store.save_call(make_call(summary="new"))
    assert call_store.get_call("call-1").summary == "new"
    assert second.created_at == first.created_at  # "received at" is kept
    assert len(call_store.list_calls()) == 1


def test_list_newest_first_with_paging(call_store):
    for n in range(5):
        call_store.save_call(make_call(f"call-{n}", started=MONDAY_9 + timedelta(hours=n)))
    assert [c.id for c in call_store.list_calls()] == ["call-4", "call-3", "call-2", "call-1", "call-0"]
    assert [c.id for c in call_store.list_calls(limit=2, offset=1)] == ["call-3", "call-2"]


def test_count_calls_in_a_time_range(call_store):
    call_store.save_call(make_call("before", started=MONDAY_9 - timedelta(days=1)))
    call_store.save_call(make_call("booked", booking_id="b1"))
    call_store.save_call(make_call("not-booked", started=MONDAY_9 + timedelta(hours=1)))
    call_store.save_call(CallRecord(id="no-start-time"))  # can't be placed in a week

    assert call_store.count_calls(MONDAY_9, MONDAY_9 + timedelta(days=7)) == (2, 1)


def test_empty_optional_fields_are_fine(call_store):
    call_store.save_call(CallRecord(id="bare"))
    loaded = call_store.get_call("bare")
    assert loaded.started_at is None and loaded.transcript is None and loaded.cost is None


# ---------- CallService: linking the booking made during the call ----------


def test_record_call_links_the_latest_booking_of_that_call(call_service, calendar):
    calendar.add_booking(make_booking("first", "call-1", 10))
    calendar.add_booking(make_booking("second", "call-1", 12))
    calendar.add_booking(make_booking("other", "call-2", 14))

    saved = call_service.record_call(make_call("call-1"))
    assert saved.booking_id == "second"
    assert call_service.get_call("call-1").booking_id == "second"


def test_record_call_without_booking(call_service):
    assert call_service.record_call(make_call("call-1")).booking_id is None
