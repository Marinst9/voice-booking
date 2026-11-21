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
    return create_engine(
        database_url,
        # Check a pooled connection still works before using it. Serverless databases (Neon) close all
        # connections when they go to sleep after 5 idle minutes; this reconnects instead of failing.
        pool_pre_ping=True,
        # Replace connections older than 4.5 minutes, so we never reuse one the database already closed
        # (Neon recommends staying below its 5-minute sleep timeout).
        pool_recycle=270,
        # Give up connecting after 10 s instead of hanging (waking Neon normally takes well under a second).
        connect_args={"connect_timeout": 10},
    )
