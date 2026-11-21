"""
A calendar stored in PostgreSQL, using SQLAlchemy 2.0.

- `BookingRow` describes the `bookings` table (the real table is created by the
  Alembic migration in migrations/versions/, run with `alembic upgrade head`).
- `PostgresCalendar` implements the same methods as the in-memory calendar.

Double bookings are prevented by the database itself: the migration adds an
EXCLUSION CONSTRAINT that says "no two *confirmed* bookings may have
overlapping time ranges". Even two server processes booking the same slot at
the same millisecond can't both succeed; the second gets SlotTakenError.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Engine, String, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column, sessionmaker

from app.calendars.base import Booking, BookingStatus, CalendarBackend, SlotTakenError
from app.config import TIMEZONE
from app.database import Base, make_engine

# PostgreSQL's error code for "exclusion constraint violated".
EXCLUSION_VIOLATION = "23P01"


class BookingRow(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("status IN ('confirmed', 'cancelled')", name="ck_bookings_status"),
        CheckConstraint("end_time > start_time", name="ck_bookings_end_after_start"),
        # + the exclusion constraint "no_overlapping_confirmed_bookings" (see the migration).
    )
    # Ask PostgreSQL to send back server-made values (created_at) right after inserting.
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    customer_name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str] = mapped_column(String(32), index=True)
    service_key: Mapped[str] = mapped_column(String(50))
    service_name: Mapped[str] = mapped_column(String(100))
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), server_default=BookingStatus.CONFIRMED.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # The Vapi call that made this booking (None for bookings made through the REST API).
    call_id: Mapped[str | None] = mapped_column(String(64), index=True)


def _to_booking(row: BookingRow) -> Booking:
    """Database row -> the plain Booking the rest of the app uses (times in Skopje)."""
    return Booking(
        id=row.id,
        customer_name=row.customer_name,
        phone=row.phone,
        service_key=row.service_key,
        service_name=row.service_name,
        start=row.start_time.astimezone(TIMEZONE),
        end=row.end_time.astimezone(TIMEZONE),
        status=BookingStatus(row.status),
        created_at=row.created_at.astimezone(TIMEZONE),
        call_id=row.call_id,
    )


class PostgresCalendar(CalendarBackend):
    def __init__(self, database_url: str | None = None, engine: Engine | None = None) -> None:
        if engine is None:
            if not database_url:
                raise ValueError("PostgresCalendar needs a database_url or an engine")
            engine = make_engine(database_url)
        self.engine = engine
        self._session = sessionmaker(engine, expire_on_commit=False)

    def list_bookings(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        include_cancelled: bool = False,
    ) -> list[Booking]:
        query = select(BookingRow).order_by(BookingRow.start_time)
        if not include_cancelled:
            query = query.where(BookingRow.status == BookingStatus.CONFIRMED)
        if start is not None:
            query = query.where(BookingRow.end_time > start)
        if end is not None:
            query = query.where(BookingRow.start_time < end)
        with self._session() as session:
            return [_to_booking(row) for row in session.scalars(query)]

    def add_booking(self, booking: Booking) -> Booking:
        row = BookingRow(
            id=booking.id,
            customer_name=booking.customer_name,
            phone=booking.phone,
            service_key=booking.service_key,
            service_name=booking.service_name,
            start_time=booking.start,
            end_time=booking.end,
            status=BookingStatus.CONFIRMED.value,
            call_id=booking.call_id,
        )
        try:
            # `begin()` commits at the end of the block, or rolls back on an error.
            with self._session.begin() as session:
                session.add(row)
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) == EXCLUSION_VIOLATION:
                raise SlotTakenError(booking.id) from exc
            raise
        return _to_booking(row)

    def find_booking(self, phone: str, start: datetime) -> Booking | None:
        query = select(BookingRow).where(
            BookingRow.phone == phone,
            BookingRow.start_time == start,
            BookingRow.status == BookingStatus.CONFIRMED,
        )
        with self._session() as session:
            row = session.scalars(query).first()
            return _to_booking(row) if row else None

    def get_booking(self, booking_id: str) -> Booking | None:
        with self._session() as session:
            row = session.get(BookingRow, booking_id)
            return _to_booking(row) if row else None

    def bookings_for_call(self, call_id: str) -> list[Booking]:
        query = select(BookingRow).where(BookingRow.call_id == call_id).order_by(BookingRow.created_at)
        with self._session() as session:
            return [_to_booking(row) for row in session.scalars(query)]

    def cancel_booking(self, booking_id: str) -> Booking:
        with self._session.begin() as session:
            row = session.get(BookingRow, booking_id)
            if row is None:
                raise KeyError(booking_id)
            row.status = BookingStatus.CANCELLED.value
        return _to_booking(row)

    def check_health(self) -> None:
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    def close(self) -> None:
        self.engine.dispose()
