"""
Shared SQLAlchemy setup for every PostgreSQL table in the app.

All table classes (BookingRow in app/calendars/postgres.py, CallRow in
app/calls/postgres.py) inherit from `Base`, so Alembic sees them all in one place.
"""

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """All database tables inherit from this. Alembic reads it to know our tables."""


def make_engine(database_url: str) -> Engine:
    # pool_pre_ping: check a pooled connection still works before using it.
    return create_engine(database_url, pool_pre_ping=True)
