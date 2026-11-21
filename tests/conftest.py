"""
Shared test setup. pytest loads this file automatically before the tests.

- Every test gets a brand-new empty calendar and a fake "now" of
  Sunday 22 November 2026, 12:00 (Skopje), so results never depend on the real date.
- Tests that use a calendar run TWICE: once with the in-memory calendar and once
  with PostgreSQL (shown as test_name[memory] and test_name[postgres]).
- The PostgreSQL runs need TEST_DATABASE_URL, e.g.
      $env:TEST_DATABASE_URL = "postgresql+psycopg://booking:booking@127.0.0.1:5432/booking_test"
  Without it they are skipped. Run only the fast ones with:  pytest -m "not postgres"
"""

import os
from datetime import datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.booking_service import BookingService
from app.calendars import CalendarBackend, InMemoryCalendar
from app.config import TIMEZONE, Settings, get_settings
from app.main import app, get_booking_service

FAKE_NOW = datetime(2026, 11, 22, 12, 0, tzinfo=TIMEZONE)  # a Sunday
TEST_API_KEY = "test-api-key-0123456789"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ---------- PostgreSQL ----------


@pytest.fixture(scope="session")
def postgres_engine():
    """One database connection pool for the whole test run, with a freshly migrated schema."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        message = "TEST_DATABASE_URL is not set (see README: 'Running the tests')."
        if os.environ.get("CI"):  # GitHub Actions sets CI=true: never silently skip the database tests there
            pytest.fail(message)
        pytest.skip(message)

    alembic_cfg = AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", url)
    alembic_cfg.attributes["skip_logging_config"] = True  # keep pytest's log capture working
    # Down to nothing and back up: this also tests that both migration directions work.
    command.downgrade(alembic_cfg, "base")
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(url)
    yield engine
    engine.dispose()


@pytest.fixture
def postgres_calendar(postgres_engine):
    from app.calendars.postgres import PostgresCalendar

    with postgres_engine.begin() as connection:
        connection.execute(text("TRUNCATE bookings"))  # empty table for every test
    return PostgresCalendar(engine=postgres_engine)


# ---------- Calendar, service and API client ----------


@pytest.fixture(params=["memory", pytest.param("postgres", marks=pytest.mark.postgres)])
def calendar(request) -> CalendarBackend:
    if request.param == "postgres":
        return request.getfixturevalue("postgres_calendar")
    return InMemoryCalendar()


@pytest.fixture
def test_settings() -> Settings:
    # _env_file=None: ignore any real .env on this computer, so tests behave the same everywhere.
    return Settings(_env_file=None, api_key=TEST_API_KEY)


@pytest.fixture
def booking_service(calendar) -> BookingService:
    return BookingService(calendar, now=lambda: FAKE_NOW)


@pytest.fixture
def client(booking_service, test_settings):
    """A test client that sends the right API key and uses the fresh `booking_service` above."""
    app.dependency_overrides[get_booking_service] = lambda: booking_service
    app.dependency_overrides[get_settings] = lambda: test_settings
    yield TestClient(app, headers={"X-API-Key": TEST_API_KEY})
    app.dependency_overrides.clear()
