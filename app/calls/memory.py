"""Call history kept in a Python dict. Lost on restart; used by the fast tests."""

from dataclasses import replace
from datetime import UTC, datetime

from app.calls.base import CallRecord, CallStore


class InMemoryCallStore(CallStore):
    def __init__(self) -> None:
        self._calls: dict[str, CallRecord] = {}

    def save_call(self, call: CallRecord) -> CallRecord:
        existing = self._calls.get(call.id)
        # Like the database: keep the original "received" time when a report is sent again.
        created_at = existing.created_at if existing else datetime.now(UTC)
        stored = replace(call, created_at=created_at)
        self._calls[call.id] = stored
        return stored

    def get_call(self, call_id: str) -> CallRecord | None:
        return self._calls.get(call_id)

    def list_calls(self, limit: int = 50, offset: int = 0) -> list[CallRecord]:
        newest_first = sorted(self._calls.values(), key=lambda c: c.sort_time, reverse=True)
        return newest_first[offset : offset + limit]

    def count_calls(self, start: datetime, end: datetime) -> tuple[int, int]:
        in_range = [c for c in self._calls.values() if c.started_at and start <= c.started_at < end]
        return len(in_range), sum(1 for c in in_range if c.booking_id)
