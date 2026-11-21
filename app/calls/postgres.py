"""
Call history stored in PostgreSQL (the `calls` table, created by migration 0002).

Saving uses INSERT ... ON CONFLICT (id) DO UPDATE ("upsert"), so a report that Vapi
sends twice for the same call updates the row instead of failing or duplicating it.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Engine, Integer, Numeric, String, Text, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column, sessionmaker

from app.calls.base import CallRecord, CallStore
from app.config import TIMEZONE
from app.database import Base, make_engine


class CallRow(Base):
    __tablename__ = "calls"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # Vapi's call id
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    ended_reason: Mapped[str | None] = mapped_column(String(100))
    transcript: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    # Numeric, not float: money should not get rounding errors like 0.1 + 0.2 = 0.30000000000000004.
    cost: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    customer_number: Mapped[str | None] = mapped_column(String(32))
    # Plain text, not a foreign key: the booking lives in the calendar, which may not be this database.
    booking_id: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# The columns we write on insert/update (created_at is set once by the database).
_WRITABLE = [
    "id",
    "started_at",
    "ended_at",
    "duration_seconds",
    "ended_reason",
    "transcript",
    "summary",
    "cost",
    "customer_number",
    "booking_id",
]


def _local(moment: datetime | None) -> datetime | None:
    return moment.astimezone(TIMEZONE) if moment else None


def _to_call(row: CallRow) -> CallRecord:
    return CallRecord(
        id=row.id,
        started_at=_local(row.started_at),
        ended_at=_local(row.ended_at),
        duration_seconds=row.duration_seconds,
        ended_reason=row.ended_reason,
        transcript=row.transcript,
        summary=row.summary,
        cost=row.cost,
        customer_number=row.customer_number,
        booking_id=row.booking_id,
        created_at=_local(row.created_at),
    )


class PostgresCallStore(CallStore):
    def __init__(self, database_url: str | None = None, engine: Engine | None = None) -> None:
        if engine is None:
            if not database_url:
                raise ValueError("PostgresCallStore needs a database_url or an engine")
            engine = make_engine(database_url)
        self.engine = engine
        self._session = sessionmaker(engine, expire_on_commit=False)

    def save_call(self, call: CallRecord) -> CallRecord:
        values = {name: getattr(call, name) for name in _WRITABLE}
        statement = insert(CallRow).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[CallRow.id],
            set_={name: statement.excluded[name] for name in _WRITABLE if name != "id"},
        ).returning(CallRow)
        with self._session.begin() as session:
            row = session.scalars(statement).one()
            return _to_call(row)

    def get_call(self, call_id: str) -> CallRecord | None:
        with self._session() as session:
            row = session.get(CallRow, call_id)
            return _to_call(row) if row else None

    def list_calls(self, limit: int = 50, offset: int = 0) -> list[CallRecord]:
        newest_first = func.coalesce(CallRow.started_at, CallRow.created_at).desc()
        query = select(CallRow).order_by(newest_first).limit(limit).offset(offset)
        with self._session() as session:
            return [_to_call(row) for row in session.scalars(query)]

    def count_calls(self, start: datetime, end: datetime) -> tuple[int, int]:
        query = select(func.count(), func.count(CallRow.booking_id)).where(
            CallRow.started_at >= start, CallRow.started_at < end
        )
        with self._session() as session:
            total, with_booking = session.execute(query).one()
            return total, with_booking

    def close(self) -> None:
        self.engine.dispose()
