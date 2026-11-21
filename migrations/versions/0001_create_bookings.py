"""Create the bookings table.

Revision ID: 0001
Revises:
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bookings",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("customer_name", sa.String(100), nullable=False),
        sa.Column("phone", sa.String(32), nullable=False),
        sa.Column("service_key", sa.String(50), nullable=False),
        sa.Column("service_name", sa.String(100), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="confirmed"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('confirmed', 'cancelled')", name="ck_bookings_status"),
        sa.CheckConstraint("end_time > start_time", name="ck_bookings_end_after_start"),
    )
    op.create_index("ix_bookings_phone", "bookings", ["phone"])

    # The double-booking guard, enforced by PostgreSQL itself:
    # "no two rows may have overlapping [start_time, end_time) ranges, among confirmed bookings".
    # '[)' means the end is not included, so 13:30-14:00 and 14:00-14:30 are allowed back to back.
    # The GiST index it creates also makes "bookings between X and Y" lookups fast.
    op.execute(
        """
        ALTER TABLE bookings
        ADD CONSTRAINT no_overlapping_confirmed_bookings
        EXCLUDE USING gist (tstzrange(start_time, end_time, '[)') WITH &&)
        WHERE (status = 'confirmed')
        """
    )


def downgrade() -> None:
    op.drop_table("bookings")
