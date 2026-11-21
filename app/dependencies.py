"""
FastAPI "dependencies" shared by the REST endpoints and the integrations (e.g. Vapi).

Kept in their own file so app/main.py and app/integrations/ can both import them
without importing each other.
"""

from fastapi import Request

from app.booking_service import BookingService


def get_booking_service(request: Request) -> BookingService:
    """Gives each endpoint the booking service made at startup (tests swap it out)."""
    return request.app.state.booking_service
