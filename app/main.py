"""
The web API: the endpoints the voice agent will call as "tools".

The endpoints are kept thin: they pass the request to BookingService and
turn the result into JSON. All rules live in booking_service.py.

Every response has:
  - "success": true/false
  - "message": a sentence the voice agent can read to the caller
"""

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.booking_service import BookingError, BookingService, say_day, say_time
from app.calendars import Booking, InMemoryCalendar
from app.models import BookingOut, CancelBookingRequest, CheckAvailabilityRequest, CreateBookingRequest

app = FastAPI(
    title="Barber Shop Booking API",
    description="Backend for an AI voice booking assistant. Uses a fake in-memory calendar.",
)

# ---- Which calendar to use ----
# To switch to Google Calendar later, only this line changes, e.g.:
#   _service = BookingService(GoogleCalendar(...))
_service = BookingService(InMemoryCalendar())


def get_booking_service() -> BookingService:
    """FastAPI 'dependency': gives each endpoint the booking service (tests swap it out)."""
    return _service


def booking_to_json(booking: Booking) -> dict:
    return BookingOut(
        id=booking.id,
        customer_name=booking.customer_name,
        phone=booking.phone,
        service=booking.service_name,
        start_time=booking.start,
        end_time=booking.end,
    ).model_dump(mode="json")


# ---- Error handling: always answer with a readable message ----

@app.exception_handler(BookingError)
def handle_booking_error(request: Request, error: BookingError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={"success": False, "error": error.code, "message": error.message},
    )


@app.exception_handler(RequestValidationError)
def handle_bad_input(request: Request, error: RequestValidationError) -> JSONResponse:
    # e.g. a missing field or a date like "next tuesday" instead of "2026-11-24"
    fields = sorted({str(e["loc"][-1]) for e in error.errors()})
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "error": "invalid_input",
            "message": f"Sorry, something was missing or unclear: {', '.join(fields)}. "
                       "Dates must look like 2026-11-23 and times like 2026-11-23T14:00.",
            "details": error.errors(),
        },
    )


# ---- Endpoints ----

@app.post("/check_availability")
def check_availability(body: CheckAvailabilityRequest, service: BookingService = Depends(get_booking_service)):
    result = service.check_availability(body.date, body.service)
    return {"success": True, **result}


@app.post("/create_booking")
def create_booking(body: CreateBookingRequest, service: BookingService = Depends(get_booking_service)):
    booking = service.create_booking(body.customer_name, body.phone, body.service, body.start_time)
    return {
        "success": True,
        "message": f"You're booked, {booking.customer_name}! {booking.service_name} on "
                   f"{say_day(booking.start.date())} at {say_time(booking.start)}.",
        "booking": booking_to_json(booking),
    }


@app.post("/cancel_booking")
def cancel_booking(body: CancelBookingRequest, service: BookingService = Depends(get_booking_service)):
    booking = service.cancel_booking(body.phone, body.start_time)
    return {
        "success": True,
        "message": f"Your {booking.service_name} on {say_day(booking.start.date())} "
                   f"at {say_time(booking.start)} has been cancelled.",
        "booking": booking_to_json(booking),
    }


@app.get("/bookings")
def list_bookings(service: BookingService = Depends(get_booking_service)):
    bookings = service.all_bookings()
    return {"success": True, "count": len(bookings), "bookings": [booking_to_json(b) for b in bookings]}
