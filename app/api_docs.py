"""
Example responses for the /docs page.

These do NOT change how the API behaves. They only tell the docs page
which status codes each endpoint can return and what they look like.
Kept in a separate file so main.py stays short and readable.
"""

from typing import Any

from app.models import ErrorResponse


def _error(summary: str, code: str, message: str, suggested: list[str] | None = None) -> dict[str, Any]:
    """One named example of an error response."""
    return {
        "summary": summary,
        "value": {"success": False, "error": code, "message": message, "suggested_times": suggested or []},
    }


def _response(description: str, **examples: dict[str, Any]) -> dict[str, Any]:
    """A documented error status code with one or more named examples."""
    return {
        "model": ErrorResponse,
        "description": description,
        "content": {"application/json": {"examples": examples}},
    }


def _success(description: str, value: dict[str, Any]) -> dict[str, Any]:
    return {"description": description, "content": {"application/json": {"example": value}}}


# ---------- Reusable error examples ----------

CLOSED_DAY = _error(
    "Closed day (Sunday)",
    "closed_day",
    "Sorry, we're closed on Sundays. We're open Monday to Saturday, 09:00 to 18:00.",
)
DATE_IN_PAST = _error(
    "Date already passed",
    "date_in_past",
    "Sorry, Friday 20 November has already passed.",
)
UNKNOWN_SERVICE = _error(
    "Unknown service",
    "unknown_service",
    "Sorry, we don't offer 'Hair colouring'. We offer Haircut, Beard trim and Haircut + Beard.",
)
OUTSIDE_HOURS = _error(
    "Outside opening hours",
    "outside_hours",
    "Sorry, a Haircut + Beard at 17:30 doesn't fit in our opening hours. On Mondays we're open "
    "09:00 to 18:00, and a Haircut + Beard takes 50 minutes. The nearest free times are 16:30 and 17:00.",
    ["16:30", "17:00"],
)
TIME_IN_PAST = _error(
    "Time already passed today",
    "time_in_past",
    "Sorry, 10:00 has already passed today. The nearest free times are 14:00 and 14:30.",
    ["14:00", "14:30"],
)
INVALID_PHONE = _error(
    "Phone number too short",
    "invalid_phone",
    "Sorry, that phone number doesn't look right. Could you repeat it?",
)
INVALID_NAME = _error(
    "Empty name",
    "invalid_name",
    "Sorry, I didn't catch your name. Could you tell me again?",
)
TIME_TAKEN = _error(
    "Slot already booked",
    "time_taken",
    "Sorry, that time is already taken. The nearest free times are 13:30 and 14:30.",
    ["13:30", "14:30"],
)
BOOKING_NOT_FOUND = _error(
    "No matching booking",
    "booking_not_found",
    "Sorry, I couldn't find a booking for that phone number at 14:00 on Monday 23 November.",
)

INVALID_INPUT = _response(
    "The request JSON is missing a field or has a badly formatted date/time.",
    invalid_input={
        "summary": "Bad date format",
        "value": {
            "success": False,
            "error": "invalid_input",
            "message": "Sorry, something was missing or unclear: date. "
            "Dates must look like 2026-11-23 and times like 2026-11-23T14:00.",
            "suggested_times": [],
            "details": [
                {
                    "type": "date_from_datetime_parsing",
                    "loc": ["body", "date"],
                    "msg": "Input should be a valid date or datetime, invalid character in year",
                    "input": "next tuesday",
                }
            ],
        },
    },
)

UNAUTHORIZED = _response(
    "The X-API-Key header is missing or wrong. Click **Authorize** at the top of this page.",
    invalid_api_key=_error(
        "Missing or wrong API key",
        "invalid_api_key",
        "Missing or invalid API key. Send it in the X-API-Key header.",
    ),
)

RATE_LIMITED = _response(
    "Too many requests from this client (RATE_LIMIT_API_PER_MINUTE). Wait for the seconds in Retry-After.",
    rate_limited=_error(
        "Too many requests", "rate_limited", "Too many requests. Please wait 12 seconds and try again."
    ),
)

EXAMPLE_BOOKING = {
    "id": "3e9aee92",
    "customer_name": "Marko",
    "phone": "+38970123456",
    "service": "Haircut",
    "start_time": "2026-11-23T14:00:00+01:00",
    "end_time": "2026-11-23T14:30:00+01:00",
    "status": "confirmed",
    "created_at": "2026-11-21T18:05:12+01:00",
}

# ---------- Per-endpoint documentation ----------

CHECK_AVAILABILITY_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: _success(
        "Free start times for that day and service.",
        {
            "success": True,
            "date": "2026-11-23",
            "service": "Haircut",
            "duration_minutes": 30,
            "free_times": ["09:00", "09:30", "10:00", "13:30", "14:30", "15:00"],
            "message": "On Monday 23 November there are 6 free times for a Haircut. "
            "The earliest are 09:00, 09:30 and 10:00, and the latest is 15:00.",
        },
    ),
    400: _response(
        "The day can't be booked or the service is unknown.",
        closed_day=CLOSED_DAY,
        date_in_past=DATE_IN_PAST,
        unknown_service=UNKNOWN_SERVICE,
    ),
    401: UNAUTHORIZED,
    429: RATE_LIMITED,
    422: INVALID_INPUT,
}

CREATE_BOOKING_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: _success(
        "The booking was created.",
        {
            "success": True,
            "message": "You're booked, Marko! Haircut on Monday 23 November at 14:00.",
            "booking": EXAMPLE_BOOKING,
        },
    ),
    400: _response(
        "The time is not bookable (closed, outside hours, in the past) or the details are invalid.",
        closed_day=CLOSED_DAY,
        outside_hours=OUTSIDE_HOURS,
        date_in_past=DATE_IN_PAST,
        time_in_past=TIME_IN_PAST,
        unknown_service=UNKNOWN_SERVICE,
        invalid_phone=INVALID_PHONE,
        invalid_name=INVALID_NAME,
    ),
    409: _response(
        "Double booking: the time overlaps an existing booking. `suggested_times` has the nearest free times.",
        time_taken=TIME_TAKEN,
    ),
    401: UNAUTHORIZED,
    429: RATE_LIMITED,
    422: INVALID_INPUT,
}

CANCEL_BOOKING_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: _success(
        "The booking was cancelled.",
        {
            "success": True,
            "message": "Your Haircut on Monday 23 November at 14:00 has been cancelled.",
            "booking": {**EXAMPLE_BOOKING, "status": "cancelled"},
        },
    ),
    404: _response("No booking matches that phone number and start time.", booking_not_found=BOOKING_NOT_FOUND),
    401: UNAUTHORIZED,
    429: RATE_LIMITED,
    422: INVALID_INPUT,
}

LIST_BOOKINGS_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: _success(
        "All bookings, sorted by start time.",
        {
            "success": True,
            "count": 1,
            "bookings": [EXAMPLE_BOOKING],
        },
    ),
    401: UNAUTHORIZED,
    429: RATE_LIMITED,
    422: INVALID_INPUT,  # e.g. ?include_cancelled=maybe
}
