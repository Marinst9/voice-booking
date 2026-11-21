"""
The web API: the endpoints the voice agent will call as "tools".

The endpoints are kept thin: they pass the request to BookingService and
turn the result into JSON. All rules live in booking_service.py.

Every response has:
  - "success": true/false
  - "message": a sentence the voice agent can read to the caller

Interactive docs: http://127.0.0.1:8000/docs
"""

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import api_docs
from app.booking_service import BookingError, BookingService, say_day, say_time
from app.calendars import Booking, InMemoryCalendar
from app.models import (
    AvailabilityResponse,
    BookingListResponse,
    BookingOut,
    BookingResponse,
    CancelBookingRequest,
    CheckAvailabilityRequest,
    CreateBookingRequest,
    ErrorResponse,
)

app = FastAPI(
    title="Barber Shop Booking API",
    description=(
        "Backend for an AI voice booking assistant. Uses a fake in-memory calendar.\n\n"
        "Shop hours: Monday-Saturday 09:00-18:00 (Europe/Skopje), closed Sunday.\n\n"
        "Every response contains `success` and a `message` the voice agent can read to the caller. "
        "Errors also contain an `error` code and, where useful, `suggested_times`."
    ),
)

# ---- Which calendar to use ----
# To switch to Google Calendar later, only this line changes, e.g.:
#   _service = BookingService(GoogleCalendar(...))
_service = BookingService(InMemoryCalendar())


def get_booking_service() -> BookingService:
    """FastAPI 'dependency': gives each endpoint the booking service (tests swap it out)."""
    return _service


def booking_to_out(booking: Booking) -> BookingOut:
    return BookingOut(
        id=booking.id,
        customer_name=booking.customer_name,
        phone=booking.phone,
        service=booking.service_name,
        start_time=booking.start,
        end_time=booking.end,
    )


# ---- Error handling: always answer with a readable message ----

@app.exception_handler(BookingError)
def handle_booking_error(request: Request, error: BookingError) -> JSONResponse:
    body = ErrorResponse(error=error.code, message=error.message, suggested_times=error.suggested_times)
    return JSONResponse(status_code=error.status_code, content=body.model_dump(exclude_none=True))


@app.exception_handler(RequestValidationError)
def handle_bad_input(request: Request, error: RequestValidationError) -> JSONResponse:
    # e.g. a missing field or a date like "next tuesday" instead of "2026-11-24"
    fields = sorted({str(e["loc"][-1]) for e in error.errors()})
    body = ErrorResponse(
        error="invalid_input",
        message=f"Sorry, something was missing or unclear: {', '.join(fields)}. "
                "Dates must look like 2026-11-23 and times like 2026-11-23T14:00.",
        details=jsonable_encoder(error.errors()),
    )
    return JSONResponse(status_code=422, content=body.model_dump())


# ---- Endpoints ----

@app.post(
    "/check_availability",
    response_model=AvailabilityResponse,
    responses=api_docs.CHECK_AVAILABILITY_RESPONSES,
    summary="List free start times for a day",
    description="Returns every start time on the given date where the chosen service fits "
                "inside opening hours without overlapping another booking. Times already "
                "passed today are left out.",
)
def check_availability(body: CheckAvailabilityRequest, service: BookingService = Depends(get_booking_service)):
    result = service.check_availability(body.date, body.service)
    return AvailabilityResponse(**result)


@app.post(
    "/create_booking",
    response_model=BookingResponse,
    responses=api_docs.CREATE_BOOKING_RESPONSES,
    summary="Book an appointment",
    description="Creates a booking if the whole appointment fits inside opening hours and does "
                "not overlap another booking. On a clash it returns **409 time_taken** with the "
                "nearest free times in `suggested_times`.",
)
def create_booking(body: CreateBookingRequest, service: BookingService = Depends(get_booking_service)):
    booking = service.create_booking(body.customer_name, body.phone, body.service, body.start_time)
    return BookingResponse(
        message=f"You're booked, {booking.customer_name}! {booking.service_name} on "
                f"{say_day(booking.start.date())} at {say_time(booking.start)}.",
        booking=booking_to_out(booking),
    )


@app.post(
    "/cancel_booking",
    response_model=BookingResponse,
    responses=api_docs.CANCEL_BOOKING_RESPONSES,
    summary="Cancel an appointment",
    description="Cancels the booking that matches the phone number and start time. "
                "Spaces and dashes in the phone number are ignored.",
)
def cancel_booking(body: CancelBookingRequest, service: BookingService = Depends(get_booking_service)):
    booking = service.cancel_booking(body.phone, body.start_time)
    return BookingResponse(
        message=f"Your {booking.service_name} on {say_day(booking.start.date())} "
                f"at {say_time(booking.start)} has been cancelled.",
        booking=booking_to_out(booking),
    )


@app.get(
    "/bookings",
    response_model=BookingListResponse,
    responses=api_docs.LIST_BOOKINGS_RESPONSES,
    summary="List all bookings",
    description="Shows every booking, sorted by start time. For the shop owner to check, "
                "not meant as a voice agent tool.",
)
def list_bookings(service: BookingService = Depends(get_booking_service)):
    bookings = service.all_bookings()
    return BookingListResponse(count=len(bookings), bookings=[booking_to_out(b) for b in bookings])
