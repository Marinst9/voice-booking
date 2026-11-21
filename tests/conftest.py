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
from app.config import TIMEZONE, Settings, get_settings
from app.main import app, get_booking_service

FAKE_NOW = datetime(2026, 11, 22, 12, 0, tzinfo=TIMEZONE)  # a Sunday
TEST_API_KEY = "test-api-key-0123456789"


@pytest.fixture
def test_settings() -> Settings:
    # _env_file=None: ignore any real .env on this computer, so tests behave the same everywhere.
    return Settings(_env_file=None, api_key=TEST_API_KEY)


@pytest.fixture
def booking_service() -> BookingService:
    return BookingService(InMemoryCalendar(), now=lambda: FAKE_NOW)


@pytest.fixture
def client(booking_service, test_settings):
    """A test client that sends the right API key and uses the fresh `booking_service` above."""
    app.dependency_overrides[get_booking_service] = lambda: booking_service
    app.dependency_overrides[get_settings] = lambda: test_settings
    yield TestClient(app, headers={"X-API-Key": TEST_API_KEY})
    app.dependency_overrides.clear()
