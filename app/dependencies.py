"""
FastAPI "dependencies" shared by the REST endpoints, the integrations (e.g. Vapi) and the admin pages.

Kept in their own file so app/main.py and app/integrations/ can both import them
without importing each other.
"""

from fastapi import Request

from app.booking_service import BookingService
from app.call_service import CallService


def get_booking_service(request: Request) -> BookingService:
    """Gives each endpoint the booking service made at startup (tests swap it out)."""
    return request.app.state.booking_service


def get_call_service(request: Request) -> CallService:
    """Gives each endpoint the call history service made at startup (tests swap it out)."""
    return request.app.state.call_service
