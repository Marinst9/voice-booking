"""
The web API: the endpoints the voice agent will call as "tools".

The endpoints are kept thin: they pass the request to BookingService and
turn the result into JSON. All rules live in booking_service.py.

Every response has:
  - "success": true/false
  - "message": a sentence the voice agent can read to the caller

Interactive docs: http://127.0.0.1:8000/docs (off in production unless DOCS_ENABLED=true)

`create_app(settings)` builds the app. Uvicorn uses the ready-made `app` at the bottom;
tests can build extra apps with other settings (e.g. production ones).
"""

import logging
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from app import api_docs
from app.admin import routes as admin
from app.admin.auth import AdminLoginRequired, CsrfError
from app.booking_service import BookingService, booked_message, cancelled_message
from app.calendars import create_calendar
from app.call_service import CallService
from app.calls import create_call_store
from app.config import Settings, get_settings
from app.dependencies import get_booking_service, get_call_service
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
from app.rate_limit import RateLimiters, limit_api
from app.security import require_api_key

settings = get_settings()
setup_logging(settings.log_level, settings.log_format)
logger = logging.getLogger("app.api")
# Requested over and over by health checks and the docs page itself: logged at DEBUG, not INFO.
QUIET_PATHS = {"/health", "/docs", "/openapi.json", "/docs/oauth2-redirect", "/redoc"}


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
    # The call history uses the same kind of storage as the calendar.
    app.state.call_service = CallService(create_call_store(current), calendar)
    log_event(
        logger,
        logging.INFO,
        "app_started",
        environment=current.environment,
        calendar_backend=current.calendar_backend,
        docs_enabled=current.show_docs,
        admin_login_enabled=current.admin_password is not None,
    )
    if current.session_secret is None:
        log_event(logger, logging.WARNING, "session_secret_missing", effect="admins are logged out on every restart")
    yield
    app.state.call_service.close()
    calendar.close()
    log_event(logger, logging.INFO, "app_stopped")


# ---- Request logging: one log line per request, with a request id ----


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
        if request.app.state.settings.is_production:
            # HTTPS only: tells browsers to never use plain http:// for this site again (for a year).
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        level = logging.DEBUG if request.url.path in QUIET_PATHS else logging.INFO
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
    return JSONResponse(
        status_code=error.status_code,
        content=body.model_dump(exclude_none=True),
        headers=error.headers,
    )


def handle_admin_login_required(request: Request, error: AdminLoginRequired) -> RedirectResponse:
    # Not logged in: go to the login page, and come back here afterwards.
    return RedirectResponse(f"/admin/login?next={quote(error.next_path)}", status_code=303)


def handle_csrf_error(request: Request, error: CsrfError) -> PlainTextResponse:
    return PlainTextResponse(
        "This form has expired. Go back, reload the page and try again.",
        status_code=403,
    )


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


# ---- Health check (for the hosting platform) ----

health_router = APIRouter(tags=["Health"])


class HealthResponse(BaseModel):
    status: str = Field(examples=["ok"])
    database: str = Field(description="ok, unreachable, or not_used (in-memory calendar)", examples=["ok"])


@health_router.get(
    "/health",
    response_model=HealthResponse,
    summary="Is the server up?",
    description="No API key needed. 200 when the server and its database work, 503 when the database "
    "can't be reached. Railway calls this after each deploy before sending traffic to the new version.",
    responses={503: {"model": HealthResponse, "description": "The database can't be reached."}},
)
def health(
    service: BookingService = Depends(get_booking_service),
    config: Settings = Depends(get_settings),
) -> JSONResponse:
    uses_database = config.calendar_backend == "postgres"
    try:
        service.check_health()
    except Exception:
        logger.exception("health_check_failed")  # the details go to the logs, not to the caller
        return JSONResponse(status_code=503, content={"status": "error", "database": "unreachable"})
    return JSONResponse(content={"status": "ok", "database": "ok" if uses_database else "not_used"})


# ---- Booking endpoints ----

# They all require X-API-Key (after the rate limit, so floods with wrong keys are limited too).
# The Vapi webhook (app/integrations/vapi.py) has its own router with its own secret check.
api = APIRouter(dependencies=[Depends(limit_api), Depends(require_api_key)])


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


# ---- Building the app ----


def create_app(config: Settings) -> FastAPI:
    docs = config.show_docs
    new_app = FastAPI(
        title="Barber Shop Booking API",
        description=(
            "Backend for an AI voice booking assistant.\n\n"
            "Shop hours: Monday-Saturday 09:00-18:00 (Europe/Skopje), closed Sunday.\n\n"
            "**Authentication:** the booking endpoints need the `X-API-Key` header. "
            "Click **Authorize** and paste your API key to try them here. "
            "`/vapi/events` and `/vapi/tools` instead check the secret Vapi sends (`VAPI_SECRET`).\n\n"
            "Every response contains `success` and a `message` the voice agent can read to the caller. "
            "Errors also contain an `error` code and, where useful, `suggested_times`."
        ),
        lifespan=lifespan,
        # DOCS_ENABLED: with None, FastAPI doesn't serve that page at all (404).
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
        # Remember the key you entered in /docs after a page reload.
        swagger_ui_parameters={"persistAuthorization": True},
    )
    new_app.state.settings = config
    new_app.state.rate_limiters = RateLimiters()

    new_app.middleware("http")(log_requests)
    # The admin dashboard's login cookie. Signed with SESSION_SECRET so it can't be forged.
    new_app.add_middleware(
        SessionMiddleware,
        secret_key=(config.session_secret.get_secret_value() if config.session_secret else secrets.token_urlsafe(32)),
        session_cookie="admin_session",
        max_age=config.session_max_age_hours * 3600,
        same_site="lax",  # not sent with form POSTs from other websites
        https_only=config.secure_cookies,  # in production: only sent over HTTPS
    )

    new_app.add_exception_handler(AppError, handle_app_error)
    new_app.add_exception_handler(AdminLoginRequired, handle_admin_login_required)
    new_app.add_exception_handler(CsrfError, handle_csrf_error)
    new_app.add_exception_handler(RequestValidationError, handle_bad_input)

    new_app.include_router(health_router)
    new_app.include_router(api)
    new_app.include_router(vapi.router)
    new_app.include_router(admin.router)  # /admin: HTML pages with their own login (app/admin/)
    return new_app


app = create_app(settings)

# The get_* ones: tests override them.
__all__ = ["app", "create_app", "get_booking_service", "get_call_service", "lifespan"]
