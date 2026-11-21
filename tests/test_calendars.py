"""
Tests for the calendar storage itself (no API, no booking rules).

The first group runs against BOTH calendars to prove they behave the same.
The last group is PostgreSQL-only and proves the database itself blocks double bookings.
"""

import threading
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.calendars import Booking, BookingStatus, SlotTakenError
from app.config import TIMEZONE


def make_booking(hour: int, minute: int = 0, minutes: int = 30, booking_id: str = "b1", phone: str = "070123456"):
    start = datetime(2026, 11, 23, hour, minute, tzinfo=TIMEZONE)
    return Booking(
        id=booking_id,
        customer_name="Marko",
        phone=phone,
        service_key="haircut",
        service_name="Haircut",
        start=start,
        end=start + timedelta(minutes=minutes),
    )


# ---------- Same behaviour for memory and postgres ----------


def test_add_and_list(calendar):
    stored = calendar.add_booking(make_booking(14))
    assert stored.status == BookingStatus.CONFIRMED
    assert stored.created_at is not None

    [listed] = calendar.list_bookings()
    assert listed.id == "b1"
    assert listed.start == datetime(2026, 11, 23, 14, 0, tzinfo=TIMEZONE)
    assert listed.start.utcoffset() == timedelta(hours=1)  # comes back in Skopje time (CET)


def test_overlap_raises_slot_taken(calendar):
    calendar.add_booking(make_booking(14, minutes=50))  # 14:00-14:50
    with pytest.raises(SlotTakenError):
        calendar.add_booking(make_booking(14, 30, booking_id="b2"))  # 14:30-15:00


def test_back_to_back_is_allowed(calendar):
    calendar.add_booking(make_booking(14))  # 14:00-14:30
    calendar.add_booking(make_booking(13, 30, booking_id="b2"))  # 13:30-14:00
    calendar.add_booking(make_booking(14, 30, booking_id="b3"))  # 14:30-15:00
    assert len(calendar.list_bookings()) == 3


def test_cancelled_booking_frees_the_slot_but_is_kept(calendar):
    calendar.add_booking(make_booking(14))
    cancelled = calendar.cancel_booking("b1")
    assert cancelled.status == BookingStatus.CANCELLED

    calendar.add_booking(make_booking(14, booking_id="b2"))  # same time is free again
    assert [b.id for b in calendar.list_bookings()] == ["b2"]
    assert {b.id for b in calendar.list_bookings(include_cancelled=True)} == {"b1", "b2"}


def test_find_booking_ignores_cancelled(calendar):
    booking = make_booking(14)
    calendar.add_booking(booking)
    assert calendar.find_booking("070123456", booking.start).id == "b1"
    calendar.cancel_booking("b1")
    assert calendar.find_booking("070123456", booking.start) is None


def test_list_window(calendar):
    calendar.add_booking(make_booking(10, booking_id="morning"))
    calendar.add_booking(make_booking(15, booking_id="afternoon"))
    noon = datetime(2026, 11, 23, 12, 0, tzinfo=TIMEZONE)
    assert [b.id for b in calendar.list_bookings(start=noon)] == ["afternoon"]
    assert [b.id for b in calendar.list_bookings(end=noon)] == ["morning"]


# ---------- PostgreSQL only: the database enforces the rules ----------


@pytest.mark.postgres
def test_database_blocks_overlap_even_without_the_app(postgres_calendar):
    postgres_calendar.add_booking(make_booking(14))
    insert = text(
        "INSERT INTO bookings (id, customer_name, phone, service_key, service_name, start_time, end_time) "
        "VALUES ('raw', 'X', '1', 'haircut', 'Haircut', "
        "'2026-11-23 14:15+01', '2026-11-23 14:45+01')"
    )
    with (
        pytest.raises(IntegrityError, match="no_overlapping_confirmed_bookings"),
        postgres_calendar.engine.begin() as connection,
    ):
        connection.execute(insert)


@pytest.mark.postgres
def test_database_rejects_unknown_status(postgres_calendar):
    insert = text(
        "INSERT INTO bookings (id, customer_name, phone, service_key, service_name, start_time, end_time, status) "
        "VALUES ('raw', 'X', '1', 'haircut', 'Haircut', '2026-11-23 10:00+01', '2026-11-23 10:30+01', 'maybe')"
    )
    with pytest.raises(IntegrityError, match="ck_bookings_status"), postgres_calendar.engine.begin() as connection:
        connection.execute(insert)


@pytest.mark.postgres
def test_simultaneous_bookings_only_one_wins(postgres_calendar):
    """Many threads try to book 14:00 at the same moment (like several servers at once)."""
    attempts = 8
    start_together = threading.Barrier(attempts)
    results: list[str] = []

    def try_to_book(n: int) -> None:
        booking = make_booking(14, booking_id=f"t{n}", phone=f"07000000{n}")
        start_together.wait()
        try:
            postgres_calendar.add_booking(booking)
            results.append("booked")
        except SlotTakenError:
            results.append("taken")

    threads = [threading.Thread(target=try_to_book, args=(n,)) for n in range(attempts)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count("booked") == 1
    assert results.count("taken") == attempts - 1
