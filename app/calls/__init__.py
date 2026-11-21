"""
Call history storage (Vapi calls), built like app/calendars/.

- `InMemoryCallStore` (memory.py): fake, for tests and quick experiments.
- `PostgresCallStore` (postgres.py): the `calls` table.

`create_call_store(settings)` picks the same kind of storage as the calendar
(CALENDAR_BACKEND), so with Docker both bookings and calls live in PostgreSQL.
"""

from app.calls.base import CallRecord, CallStore
from app.calls.memory import InMemoryCallStore
from app.config import Settings


def create_call_store(settings: Settings) -> CallStore:
    if settings.calendar_backend == "postgres":
        from app.calls.postgres import PostgresCallStore

        return PostgresCallStore(settings.database_url)
    return InMemoryCallStore()


__all__ = ["CallRecord", "CallStore", "InMemoryCallStore", "create_call_store"]
