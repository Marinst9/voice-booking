"""
Automated tests. Run them with:  python -m pytest

Each test gets a brand-new empty calendar and a fake "now" of
Sunday 22 November 2026, 12:00 (Skopje), so results never depend on the real date.
"""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.booking_service import BookingService
from app.calendars import InMemoryCalendar
from app.config import TIMEZONE
from app.main import app, get_booking_service

FAKE_NOW = datetime(2026, 11, 22, 12, 0, tzinfo=TIMEZONE)  # a Sunday
MONDAY = "2026-11-23"
SUNDAY = "2026-11-29"


@pytest.fixture
def client():
    """A test client whose API uses a fresh, empty calendar."""
    fresh_service = BookingService(InMemoryCalendar(), now=lambda: FAKE_NOW)
    app.dependency_overrides[get_booking_service] = lambda: fresh_service
    yield TestClient(app)
    app.dependency_overrides.clear()


def book(client, time="14:00", service="Haircut", phone="+389 70 123 456", name="Marko", day=MONDAY):
    """Small helper so tests stay short."""
    return client.post("/create_booking", json={
        "customer_name": name,
        "phone": phone,
        "service": service,
        "start_time": f"{day}T{time}",
    })


# ---------- free slot ----------

def test_empty_day_has_all_slots(client):
    response = client.post("/check_availability", json={"date": MONDAY, "service": "Haircut"})
    assert response.status_code == 200
    times = response.json()["free_times"]
    assert times[0] == "09:00"
    assert times[-1] == "17:30"   # 30-minute haircut must end by 18:00
    assert len(times) == 18


def test_longer_service_has_earlier_last_slot(client):
    response = client.post("/check_availability", json={"date": MONDAY, "service": "Haircut + Beard"})
    assert response.json()["free_times"][-1] == "17:00"  # 17:00 + 50 min = 17:50


def test_book_free_slot(client):
    response = book(client, "14:00")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "14:00" in data["message"]
    assert data["booking"]["end_time"].startswith("2026-11-23T14:30")


def test_booked_time_disappears_from_availability(client):
    book(client, "14:00")
    times = client.post("/check_availability", json={"date": MONDAY, "service": "Haircut"}).json()["free_times"]
    assert "14:00" not in times
    assert "13:30" in times and "14:30" in times


# ---------- double booking ----------

def test_double_booking_rejected_with_suggestions(client):
    book(client, "14:00")
    response = book(client, "14:00", phone="070999888", name="Ana")
    assert response.status_code == 409
    data = response.json()
    assert data["success"] is False
    assert data["error"] == "time_taken"
    assert data["message"] == "Sorry, that time is already taken. The nearest free times are 13:30 and 14:30."


def test_overlapping_booking_rejected(client):
    book(client, "14:00", service="Haircut + Beard")       # 14:00 - 14:50
    response = book(client, "14:30", service="Beard trim")  # would start inside it
    assert response.status_code == 409


def test_back_to_back_bookings_allowed(client):
    assert book(client, "14:00").status_code == 200   # 14:00 - 14:30
    assert book(client, "14:30").status_code == 200   # 14:30 - 15:00


# ---------- closed day ----------

def test_sunday_availability_is_closed(client):
    response = client.post("/check_availability", json={"date": SUNDAY, "service": "Haircut"})
    assert response.status_code == 400
    assert response.json()["error"] == "closed_day"
    assert "closed on Sundays" in response.json()["message"]


def test_sunday_booking_rejected(client):
    response = book(client, "10:00", day=SUNDAY)
    assert response.status_code == 400
    assert response.json()["error"] == "closed_day"


# ---------- outside opening hours ----------

def test_before_opening_rejected(client):
    response = book(client, "08:00")
    assert response.status_code == 400
    assert response.json()["error"] == "outside_hours"
    assert "09:00" in response.json()["message"]  # suggests the first free time


def test_service_running_past_closing_rejected(client):
    response = book(client, "17:30", service="Haircut + Beard")  # would end 18:20
    assert response.status_code == 400
    assert response.json()["error"] == "outside_hours"


def test_service_ending_exactly_at_closing_allowed(client):
    assert book(client, "17:30", service="Haircut").status_code == 200  # ends 18:00


# ---------- cancelling ----------

def test_cancel_booking(client):
    book(client, "14:00", phone="+389 70 123 456")
    # Same number written differently still matches.
    response = client.post("/cancel_booking", json={"phone": "+38970-123-456", "start_time": f"{MONDAY}T14:00"})
    assert response.status_code == 200
    assert client.get("/bookings").json()["count"] == 0


def test_cancel_unknown_booking(client):
    response = client.post("/cancel_booking", json={"phone": "070111222", "start_time": f"{MONDAY}T14:00"})
    assert response.status_code == 404
    assert response.json()["error"] == "booking_not_found"


# ---------- other checks ----------

def test_list_bookings(client):
    book(client, "15:00", name="Ana")
    book(client, "10:00", name="Marko")
    data = client.get("/bookings").json()
    assert data["count"] == 2
    assert [b["customer_name"] for b in data["bookings"]] == ["Marko", "Ana"]  # sorted by time


def test_unknown_service(client):
    response = book(client, "14:00", service="Hair colouring")
    assert response.status_code == 400
    assert response.json()["error"] == "unknown_service"


def test_service_name_variants_accepted(client):
    assert book(client, "10:00", service="haircut and beard").status_code == 200
    assert book(client, "12:00", service="beard_trim").status_code == 200


def test_past_date_rejected(client):
    response = book(client, "10:00", day="2026-11-20")
    assert response.json()["error"] == "date_in_past"


def test_bad_date_format(client):
    response = client.post("/check_availability", json={"date": "next tuesday", "service": "Haircut"})
    assert response.status_code == 422
    assert response.json()["success"] is False
