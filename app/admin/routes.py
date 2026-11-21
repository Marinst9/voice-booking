"""
The admin dashboard: server-rendered HTML pages (Jinja2 templates in app/admin/templates/).

    GET  /admin                  today's and upcoming bookings + this week's stats
    POST /admin/bookings/{id}/cancel
    GET  /admin/calls            call history
    GET  /admin/calls/{id}       one call: transcript and summary
    GET  /admin/login, POST /admin/login, POST /admin/logout

Like the REST endpoints, these pages only read from / ask the services; no rules live here.
They are hidden from /docs (include_in_schema=False) because they return HTML, not JSON.
"""

import logging
from datetime import datetime, time, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.admin import views
from app.admin.auth import (
    check_credentials,
    check_csrf,
    csrf_token,
    current_admin,
    log_in,
    log_out,
    require_admin,
    safe_next_path,
)
from app.booking_service import BookingService
from app.call_service import CallService
from app.config import TIMEZONE, Settings, get_settings
from app.dependencies import get_booking_service, get_call_service
from app.errors import AppError
from app.logging_config import log_event

logger = logging.getLogger("app.admin")

CALLS_PER_PAGE = 25

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")  # autoescapes HTML by default
templates.env.filters.update(
    when=views.when,
    duration=views.duration,
    money=views.money,
    ended_reason=views.ended_reason,
)
templates.env.globals.update(csrf_token=csrf_token)

router = APIRouter(prefix="/admin", include_in_schema=False)


def render(request: Request, template: str, status_code: int = 200, **context) -> HTMLResponse:
    """Render a page. Adds the logged-in user and any one-time "flash" message from the session."""
    context.setdefault("admin", request.session.get("admin"))
    context["flash"] = request.session.pop("flash", None)
    response = templates.TemplateResponse(request, template, context, status_code=status_code)
    # Pages contain customer data: don't let the browser cache them, or other sites frame them.
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Frame-Options"] = "DENY"
    return response


def redirect(path: str) -> RedirectResponse:
    # 303 = "go and GET this page", the normal answer after a form POST.
    return RedirectResponse(path, status_code=303)


def flash(request: Request, message: str) -> None:
    """A message shown once on the next page ("Booking cancelled.")."""
    request.session["flash"] = message


# ---------- Login ----------


@router.get("/login")
def login_page(request: Request, next: str | None = None, settings: Settings = Depends(get_settings)) -> Response:
    if current_admin(request, settings):
        return redirect(safe_next_path(next))
    return render(
        request,
        "login.html",
        next=safe_next_path(next),
        login_enabled=settings.admin_password is not None,
        error=None,
    )


@router.post("/login")
def login(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next: str = Form("/admin"),
    csrf: str | None = Form(None),
    settings: Settings = Depends(get_settings),
) -> Response:
    check_csrf(request, csrf)
    if not check_credentials(username, password, settings):
        log_event(logger, logging.WARNING, "admin_login_failed", username=username[:50])
        return render(
            request,
            "login.html",
            status_code=401,
            next=safe_next_path(next),
            login_enabled=settings.admin_password is not None,
            error="Wrong username or password.",
            username=username,
        )
    log_in(request, settings.admin_username)
    return redirect(safe_next_path(next))


@router.post("/logout")
def logout(request: Request, csrf: str | None = Form(None)) -> Response:
    check_csrf(request, csrf)
    log_out(request)
    return redirect("/admin/login")


# ---------- Bookings and stats ----------


def week_bounds(now: datetime) -> tuple[datetime, datetime]:
    """Monday 00:00 of this week and of next week, in shop time."""
    monday = now.date() - timedelta(days=now.weekday())
    start = datetime.combine(monday, time(0), tzinfo=TIMEZONE)
    return start, start + timedelta(days=7)


@router.get("")
def dashboard(
    request: Request,
    admin: str = Depends(require_admin),
    bookings: BookingService = Depends(get_booking_service),
    calls: CallService = Depends(get_call_service),
) -> Response:
    now = bookings.now()
    today_start = datetime.combine(now.date(), time(0), tzinfo=TIMEZONE)
    tomorrow_start = today_start + timedelta(days=1)
    week_start, week_end = week_bounds(now)

    calls_this_week, booked_calls = calls.call_counts(week_start, week_end)
    stats = {
        "bookings_this_week": len(bookings.bookings_between(week_start, week_end)),
        "calls_this_week": calls_this_week,
        "booking_rate": views.percent(booked_calls, calls_this_week),
        "booked_calls": booked_calls,
    }
    return render(
        request,
        "dashboard.html",
        now=now,
        week_start=week_start,
        week_end=week_end - timedelta(days=1),
        stats=stats,
        today=bookings.bookings_between(today_start, tomorrow_start),
        upcoming=bookings.bookings_between(tomorrow_start),
    )


@router.post("/bookings/{booking_id}/cancel")
def cancel_booking(
    request: Request,
    booking_id: str,
    csrf: str | None = Form(None),
    admin: str = Depends(require_admin),
    bookings: BookingService = Depends(get_booking_service),
) -> Response:
    check_csrf(request, csrf)
    try:
        booking = bookings.cancel_booking_by_id(booking_id)
    except AppError as error:
        flash(request, error.message)
    else:
        flash(
            request,
            f"Cancelled: {booking.customer_name}, {booking.service_name} on {views.when(booking.start)}.",
        )
    return redirect("/admin")


# ---------- Calls ----------


@router.get("/calls")
def call_list(
    request: Request,
    page: int = 1,
    admin: str = Depends(require_admin),
    calls: CallService = Depends(get_call_service),
) -> Response:
    page = max(page, 1)
    # Ask for one extra call: if it exists, there is a next page.
    found = calls.recent_calls(limit=CALLS_PER_PAGE + 1, offset=(page - 1) * CALLS_PER_PAGE)
    return render(
        request,
        "calls.html",
        calls=found[:CALLS_PER_PAGE],
        page=page,
        has_next=len(found) > CALLS_PER_PAGE,
    )


@router.get("/calls/{call_id}")
def call_detail(
    request: Request,
    call_id: str,
    admin: str = Depends(require_admin),
    calls: CallService = Depends(get_call_service),
    bookings: BookingService = Depends(get_booking_service),
) -> Response:
    call = calls.get_call(call_id)
    if call is None:
        return render(request, "not_found.html", status_code=404, what="call")
    booking = bookings.get_booking(call.booking_id) if call.booking_id else None
    return render(
        request,
        "call_detail.html",
        call=call,
        booking=booking,
        lines=views.transcript_lines(call.transcript),
    )
