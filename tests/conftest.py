"""
Shared test setup. pytest loads this file automatically before the tests.

Every test gets a brand-new empty calendar and a fake "now" of
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


@pytest.fixture
def booking_service() -> BookingService:
    return BookingService(InMemoryCalendar(), now=lambda: FAKE_NOW)


@pytest.fixture
def client(booking_service):
    """A test client whose API uses the fresh `booking_service` above."""
    app.dependency_overrides[get_booking_service] = lambda: booking_service
    yield TestClient(app)
    app.dependency_overrides.clear()
