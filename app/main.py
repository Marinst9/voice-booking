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

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import api_docs
from app.booking_service import BookingService, booked_message, cancelled_message
from app.calendars import create_calendar
from app.config import get_settings
from app.dependencies import get_booking_service
from app.errors import AppError, invalid_input_message
from app.integrations import vapi
from app.logging_config import log_event, request_id_var, setup_logging
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
from app.security import require_api_key

settings = get_settings()
setup_logging(settings.log_level, settings.log_format)
logger = logging.getLogger("app.api")
DOCS_PATHS = {"/docs", "/openapi.json", "/docs/oauth2-redirect", "/redoc"}


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
        "**Authentication:** the booking endpoints need the `X-API-Key` header. "
        "Click **Authorize** and paste your API key to try them here. "
        "`/vapi/tools` instead checks the secret Vapi sends (`VAPI_SECRET`).\n\n"
        "Every response contains `success` and a `message` the voice agent can read to the caller. "
        "Errors also contain an `error` code and, where useful, `suggested_times`."
    ),
    lifespan=lifespan,
    # Remember the key you entered in /docs after a page reload.
    swagger_ui_parameters={"persistAuthorization": True},
)

# The booking endpoints all require X-API-Key. /docs and /openapi.json are not on this router, so they stay open.
# The Vapi webhook (app/integrations/vapi.py) has its own router with its own secret check.
api = APIRouter(dependencies=[Depends(require_api_key)])


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
        # The docs pages are fetched every few seconds by Docker's health check: keep them out of INFO logs.
        level = logging.DEBUG if request.url.path in DOCS_PATHS else logging.INFO
        log_event(
            logger,
            level,
            "request_completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        return response
    finally:
        request_id_var.reset(token)


# ---- Error handling: always answer with a readable message ----


@app.exception_handler(AppError)
def handle_app_error(request: Request, error: AppError) -> JSONResponse:
    log_event(
        logger,
        logging.WARNING,
        "request_rejected",
        error=error.code,
        status_code=error.status_code,
        path=request.url.path,
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
        message=invalid_input_message(fields),
        details=jsonable_encoder(error.errors()),
    )
    return JSONResponse(status_code=422, content=body.model_dump())


# ---- Endpoints ----


@api.post(
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


@api.post(
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
    return BookingResponse(message=booked_message(booking), booking=BookingOut.from_booking(booking))


@api.post(
    "/cancel_booking",
    response_model=BookingResponse,
    responses=api_docs.CANCEL_BOOKING_RESPONSES,
    summary="Cancel an appointment",
    description="Cancels the booking that matches the phone number and start time. "
    "Spaces and dashes in the phone number are ignored.",
)
def cancel_booking(body: CancelBookingRequest, service: BookingService = Depends(get_booking_service)):
    booking = service.cancel_booking(body.phone, body.start_time)
    return BookingResponse(message=cancelled_message(booking), booking=BookingOut.from_booking(booking))


@api.get(
    "/bookings",
    response_model=BookingListResponse,
    responses=api_docs.LIST_BOOKINGS_RESPONSES,
    summary="List all bookings",
    description="Shows confirmed bookings, sorted by start time. Add `?include_cancelled=true` "
    "to also see cancelled ones. For the shop owner to check, not meant as a voice agent tool.",
)
def list_bookings(include_cancelled: bool = False, service: BookingService = Depends(get_booking_service)):
    bookings = service.all_bookings(include_cancelled=include_cancelled)
    return BookingListResponse(count=len(bookings), bookings=[BookingOut.from_booking(b) for b in bookings])


app.include_router(api)
app.include_router(vapi.router)

__all__ = ["app", "get_booking_service", "lifespan"]  # get_booking_service: tests override it
