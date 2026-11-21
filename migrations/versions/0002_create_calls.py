"""Add the calls table (Vapi call history) and bookings.call_id.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Which Vapi call made a booking. Nullable: bookings made through the REST API have none.
    op.add_column("bookings", sa.Column("call_id", sa.String(64), nullable=True))
    op.create_index("ix_bookings_call_id", "bookings", ["call_id"])

    op.create_table(
        "calls",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("ended_reason", sa.String(100), nullable=True),
        sa.Column("transcript", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("cost", sa.Numeric(10, 4), nullable=True),
        sa.Column("customer_number", sa.String(32), nullable=True),
        sa.Column("booking_id", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    # The dashboard lists calls newest first and counts "calls this week" by start time.
    op.create_index("ix_calls_started_at", "calls", ["started_at"])


def downgrade() -> None:
    op.drop_table("calls")
    op.drop_index("ix_bookings_call_id", table_name="bookings")
    op.drop_column("bookings", "call_id")
