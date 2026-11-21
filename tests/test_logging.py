"""Tests that important things are logged as structured events."""

import json
import logging

from app.logging_config import JsonFormatter, mask_phone

BOOKING = {
    "customer_name": "Marko",
    "phone": "+389 70 123 456",
    "service": "Haircut",
    "start_time": "2026-11-23T14:00",
}


def events(caplog, name):
    """All captured log records with this event name."""
    return [r for r in caplog.records if r.getMessage() == name]


def test_booking_and_request_are_logged(client, caplog):
    caplog.set_level(logging.INFO)
    response = client.post("/create_booking", json=BOOKING)

    [created] = events(caplog, "booking_created")
    assert created.fields["service"] == "haircut"
    assert created.fields["phone"] == "***456"  # never the full number

    [request] = events(caplog, "request_completed")
    assert request.fields["path"] == "/create_booking"
    assert request.fields["status_code"] == 200
    assert response.headers["X-Request-ID"]


def test_cancellation_is_logged(client, caplog):
    client.post("/create_booking", json=BOOKING)
    caplog.set_level(logging.INFO)
    client.post("/cancel_booking", json={"phone": BOOKING["phone"], "start_time": BOOKING["start_time"]})
    assert len(events(caplog, "booking_cancelled")) == 1


def test_rejection_is_logged_as_warning(client, caplog):
    caplog.set_level(logging.INFO)
    client.post("/create_booking", json={**BOOKING, "start_time": "2026-11-29T10:00"})  # Sunday
    [rejected] = events(caplog, "request_rejected")
    assert rejected.levelno == logging.WARNING
    assert rejected.fields["error"] == "closed_day"


def test_unexpected_error_returns_friendly_500_and_is_logged(client, booking_service, caplog, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("database exploded")

    monkeypatch.setattr(booking_service, "check_availability", broken)
    response = client.post("/check_availability", json={"date": "2026-11-23", "service": "Haircut"})

    assert response.status_code == 500
    assert response.json()["error"] == "internal_error"
    assert "database exploded" not in response.text  # internal details stay in the logs
    [error] = events(caplog, "unhandled_error")
    assert error.exc_info is not None


def test_request_id_from_caller_is_reused(client):
    response = client.get("/bookings", headers={"X-Request-ID": "call-42"})
    assert response.headers["X-Request-ID"] == "call-42"


def test_json_formatter_output():
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "something_happened", None, None)
    record.fields = {"booking_id": "ab12"}
    line = json.loads(JsonFormatter().format(record))
    assert line["event"] == "something_happened"
    assert line["booking_id"] == "ab12"
    assert line["level"] == "INFO"


def test_mask_phone():
    assert mask_phone("+38970123456") == "***456"
    assert mask_phone("12") == "***"
