"""
The web API: the endpoints the voice agent will call as "tools".

The endpoints are kept thin: they pass the request to BookingService and
turn the result into JSON. All rules live in booking_service.py.

Every response has:
  - "success": true/false
  - "message": a sentence the voice agent can read to the caller

Interactive docs: http://127.0.0.1:8000/docs
"""

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import api_docs
from app.booking_service import BookingService, say_day, say_time
from app.calendars import Booking, create_calendar
from app.config import get_settings
from app.errors import AppError
from app.logging_config import log_event, request_id_var, setup_logging
from app.security import require_api_key
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

settings = get_settings()
setup_logging(settings.log_level, settings.log_format)
logger = logging.getLogger("app.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs once when the server starts (before `yield`) and stops (after)."""
    current = get_settings()
    if current.api_key is None:
        raise RuntimeError("API_KEY is not set. Copy .env.example to .env and set API_KEY.")

    # ---- Which calendar to use: decided by CALENDAR_BACKEND (memory or postgres) ----
    # To add Google Calendar later, add it to create_calendar(); nothing here changes.
    calendar = create_calendar(current)
    app.state.booking_service = BookingService(calendar)
    log_event(logger, logging.INFO, "app_started", calendar_backend=current.calendar_backend)
    yield
    calendar.close()
    log_event(logger, logging.INFO, "app_stopped")


app = FastAPI(
    title="Barber Shop Booking API",
    description=(
        "Backend for an AI voice booking assistant.\n\n"
        "Shop hours: Monday-Saturday 09:00-18:00 (Europe/Skopje), closed Sunday.\n\n"
        "**Authentication:** every endpoint needs the `X-API-Key` header. "
        "Click **Authorize** and paste your API key to try them here.\n\n"
        "Every response contains `success` and a `message` the voice agent can read to the caller. "
        "Errors also contain an `error` code and, where useful, `suggested_times`."
    ),
    lifespan=lifespan,
    # Applies to every route below. /docs and /openapi.json are not routes of ours, so they stay open.
    dependencies=[Depends(require_api_key)],
    # Remember the key you entered in /docs after a page reload.
    swagger_ui_parameters={"persistAuthorization": True},
)

def get_booking_service(request: Request) -> BookingService:
    """FastAPI 'dependency': gives each endpoint the booking service made at startup (tests swap it out)."""
    return request.app.state.booking_service


def booking_to_out(booking: Booking) -> BookingOut:
    return BookingOut(
        id=booking.id,
        customer_name=booking.customer_name,
        phone=booking.phone,
        service=booking.service_name,
        start_time=booking.start,
        end_time=booking.end,
        status=booking.status.value,
        created_at=booking.created_at,
    )


# ---- Request logging: one log line per request, with a request id ----

@app.middleware("http")
async def log_requests(request: Request, call_next):
    # Reuse the caller's X-Request-ID if it sent one, so logs can be matched across systems.
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    token = request_id_var.set(request_id)
    started = time.perf_counter()
    try:
        try:
            response = await call_next(request)
        except Exception:
            # A bug we didn't expect: log the full error, but give the caller a calm answer.
            logger.exception("unhandled_error", extra={"fields": {"path": request.url.path}})
            body = ErrorResponse(
                error="internal_error",
                message="Sorry, something went wrong on our side. Please try again in a moment.",
            )
            response = JSONResponse(status_code=500, content=body.model_dump(exclude_none=True))
        response.headers["X-Request-ID"] = request_id
        log_event(
            logger, logging.INFO, "request_completed",
            method=request.method, path=request.url.path, status_code=response.status_code,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        return response
    finally:
        request_id_var.reset(token)


# ---- Error handling: always answer with a readable message ----

@app.exception_handler(AppError)
def handle_app_error(request: Request, error: AppError) -> JSONResponse:
    log_event(
        logger, logging.WARNING, "request_rejected",
        error=error.code, status_code=error.status_code, path=request.url.path,
    )
    body = ErrorResponse(error=error.code, message=error.message, suggested_times=error.suggested_times)
    return JSONResponse(status_code=error.status_code, content=body.model_dump(exclude_none=True))


@app.exception_handler(RequestValidationError)
def handle_bad_input(request: Request, error: RequestValidationError) -> JSONResponse:
    # e.g. a missing field or a date like "next tuesday" instead of "2026-11-24"
    fields = sorted({str(e["loc"][-1]) for e in error.errors()})
    log_event(logger, logging.WARNING, "invalid_input", fields=fields, path=request.url.path)
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
    description="Shows confirmed bookings, sorted by start time. Add `?include_cancelled=true` "
                "to also see cancelled ones. For the shop owner to check, not meant as a voice agent tool.",
)
def list_bookings(include_cancelled: bool = False, service: BookingService = Depends(get_booking_service)):
    bookings = service.all_bookings(include_cancelled=include_cancelled)
    return BookingListResponse(count=len(bookings), bookings=[booking_to_out(b) for b in bookings])
