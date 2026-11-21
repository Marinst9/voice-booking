"""
Shared test setup. pytest loads this file automatically before the tests.

- Every test gets a brand-new empty calendar and call history, and a fake "now" of
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
from app.call_service import CallService
from app.calls import CallStore, InMemoryCallStore
from app.config import TIMEZONE, Settings, get_settings
from app.main import app, get_booking_service, get_call_service

FAKE_NOW = datetime(2026, 11, 22, 12, 0, tzinfo=TIMEZONE)  # a Sunday
TEST_API_KEY = "test-api-key-0123456789"
TEST_VAPI_SECRET = "test-vapi-secret-0123456789"
TEST_ADMIN_USERNAME = "owner"
TEST_ADMIN_PASSWORD = "test-admin-password-123"
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
def clean_postgres(postgres_engine):
    """Empty tables for every test."""
    with postgres_engine.begin() as connection:
        connection.execute(text("TRUNCATE bookings, calls"))
    return postgres_engine


@pytest.fixture
def postgres_calendar(clean_postgres):
    from app.calendars.postgres import PostgresCalendar

    return PostgresCalendar(engine=clean_postgres)


@pytest.fixture
def postgres_call_store(clean_postgres):
    from app.calls.postgres import PostgresCallStore

    return PostgresCallStore(engine=clean_postgres)


# ---------- Storage, services and API client ----------


@pytest.fixture(params=["memory", pytest.param("postgres", marks=pytest.mark.postgres)])
def backend(request) -> str:
    """Which storage this test run uses. The calendar and the call store always use the same one."""
    return request.param


@pytest.fixture
def calendar(backend, request) -> CalendarBackend:
    if backend == "postgres":
        return request.getfixturevalue("postgres_calendar")
    return InMemoryCalendar()


@pytest.fixture
def call_store(backend, request) -> CallStore:
    if backend == "postgres":
        return request.getfixturevalue("postgres_call_store")
    return InMemoryCallStore()


@pytest.fixture
def call_service(call_store, calendar) -> CallService:
    return CallService(call_store, calendar)


@pytest.fixture
def test_settings() -> Settings:
    # _env_file=None: ignore any real .env on this computer, so tests behave the same everywhere.
    return Settings(
        _env_file=None,
        api_key=TEST_API_KEY,
        vapi_secret=TEST_VAPI_SECRET,
        admin_username=TEST_ADMIN_USERNAME,
        admin_password=TEST_ADMIN_PASSWORD,
    )


@pytest.fixture
def booking_service(calendar) -> BookingService:
    return BookingService(calendar, now=lambda: FAKE_NOW)


@pytest.fixture
def client(booking_service, call_service, test_settings):
    """A test client that sends the right API key and uses the fresh services above."""
    app.dependency_overrides[get_booking_service] = lambda: booking_service
    app.dependency_overrides[get_call_service] = lambda: call_service
    app.dependency_overrides[get_settings] = lambda: test_settings
    yield TestClient(app, headers={"X-API-Key": TEST_API_KEY})
    app.dependency_overrides.clear()
