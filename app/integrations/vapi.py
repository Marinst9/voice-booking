"""
Vapi adapter: lets a Vapi voice assistant use the booking service as "tools",
and saves each finished call to the call history.

Vapi sends "server messages" to a Server URL (https://docs.vapi.ai/server-url/events).
Every message is {"message": {"type": "...", ...}}. We handle two types:
  - "tool-calls": the assistant wants to use a tool (below). Needs an answer.
  - "end-of-call-report": the call is over; contains transcript, summary, cost, ...
    We save it (see "End-of-call report" further down). Needs no answer.
Everything else (status updates, ...) is acknowledged and ignored.

Two URLs, same handler:
  - /vapi/events  -> the assistant's Server URL (receives every message type)
  - /vapi/tools   -> the tools' Server URL (from the previous version; still works)

How a tool call works (https://docs.vapi.ai/tools/custom-tools):

1. During a call, the assistant decides to use a tool. Vapi POSTs to the Server URL:
       {"message": {"type": "tool-calls",
                    "toolCallList": [{"id": "call_1", "type": "function",
                                      "function": {"name": "check_availability",
                                                   "arguments": {"date": "2026-11-23", "service": "Haircut"}}}],
                    "call": {...}, ...}}
2. We run each tool call against the normal BookingService (same rules as the REST API).
3. We answer with HTTP 200 and one result per call:
       {"results": [{"toolCallId": "call_1", "name": "check_availability", "result": "<one-line JSON string>"}]}

Rules from the Vapi docs that this file follows:
- Always HTTP 200 (Vapi ignores other status codes, and the assistant hears "no result").
- `result` must be a single-line string, so we send compact JSON.
- Every result echoes the `toolCallId` it answers.

Where errors go:
- Booking outcomes like "time taken" or "closed on Sundays" are normal answers the assistant must
  read to the caller, so they go in `result` (with success=false, message and suggested_times).
- Only real failures (unknown tool, unexpected crash) go in `error`.

This file only translates formats. All booking rules stay in app/booking_service.py,
and the call history rules in app/call_service.py.
"""

import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.booking_service import BookingService, booked_message, cancelled_message, say_day, say_time
from app.call_service import CallService
from app.calls import CallRecord
from app.config import TIMEZONE, settings
from app.dependencies import get_booking_service, get_call_service
from app.errors import AppError, invalid_input_message
from app.logging_config import log_event
from app.models import (
    AvailabilityResponse,
    BookingOut,
    BookingResponse,
    CancelBookingRequest,
    CheckAvailabilityRequest,
    CreateBookingRequest,
    ErrorResponse,
)
from app.security import require_vapi_secret

logger = logging.getLogger("app.vapi")

# How many days ahead get_current_datetime lists, so the assistant can map "next Friday" to a date.
UPCOMING_DAYS = 14


# ======================= What Vapi sends =======================
# Field names are camelCase because that's how Vapi writes them.
# extra="allow": Vapi sends many more fields (call, assistant, artifact, ...); we keep but ignore them.


class VapiFunctionCall(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    # Normally an object; some setups send it as a JSON string, so we accept both.
    arguments: dict[str, Any] | str = Field(default_factory=dict)


class VapiToolCall(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    type: str = "function"
    function: VapiFunctionCall


class VapiMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    toolCallList: list[VapiToolCall] = Field(default_factory=list)  # Vapi's field name
    call: dict[str, Any] | None = None
    # The end-of-call-report fields are read from the extras with _field() below, on purpose
    # without strict types: one oddly formatted field must not make us lose the whole report.


class VapiWebhook(BaseModel):
    message: VapiMessage


# ======================= What we send back =======================


class VapiToolResult(BaseModel):
    toolCallId: str
    name: str
    result: str | None = Field(None, description="Single-line JSON string the assistant reads")
    error: str | None = Field(None, description="Only for real failures (unknown tool, server error)")


class VapiResponse(BaseModel):
    # Only tool-calls get results. Other messages (end-of-call-report, ...) get an empty `{}`.
    results: list[VapiToolResult] | None = None


# ======================= Tool: get_current_datetime =======================


class DayInfo(BaseModel):
    date: str
    weekday: str
    open: bool
    opening_hours: str | None  # "09:00-18:00", or None when closed


class CurrentDateTimeResponse(BaseModel):
    success: bool = True
    now: str
    date: str
    weekday: str
    time: str
    timezone: str
    open_now: bool
    upcoming_days: list[DayInfo]
    message: str


def get_current_datetime(service: BookingService) -> CurrentDateTimeResponse:
    """
    Today's date and time in Skopje, plus the next two weeks with weekday names.

    Language models are bad at calendar maths ("what date is next Monday?"), so instead
    of making the assistant calculate, we give it a ready-made list to look the date up in.
    """
    now = service.now()
    days = []
    for offset in range(UPCOMING_DAYS):
        day = now.date() + timedelta(days=offset)
        window = service.opening_window(day)
        days.append(
            DayInfo(
                date=day.isoformat(),
                weekday=f"{day:%A}",
                open=window is not None,
                opening_hours=f"{say_time(window[0])}-{say_time(window[1])}" if window else None,
            )
        )
    today = service.opening_window(now.date())
    open_now = today is not None and today[0] <= now < today[1]
    return CurrentDateTimeResponse(
        now=now.isoformat(timespec="minutes"),
        date=now.date().isoformat(),
        weekday=f"{now:%A}",
        time=say_time(now),
        timezone=settings.shop_timezone,
        open_now=open_now,
        upcoming_days=days,
        message=f"It's {say_day(now.date())} {now.year}, {say_time(now)} in Skopje. "
        f"The shop is {'open' if open_now else 'closed'} right now.",
    )


# ======================= Tool dispatch =======================
# Each tool: (booking service, arguments dict, Vapi call id) -> a response model.
# The REST endpoints and these tools return the exact same JSON shapes.


def _check_availability(service: BookingService, args: dict[str, Any], call_id: str | None) -> BaseModel:
    body = CheckAvailabilityRequest.model_validate(args)
    return AvailabilityResponse(**service.check_availability(body.date, body.service))


def _create_booking(service: BookingService, args: dict[str, Any], call_id: str | None) -> BaseModel:
    body = CreateBookingRequest.model_validate(args)
    # The call id is stored on the booking, so the call history can show "this call led to a booking".
    booking = service.create_booking(body.customer_name, body.phone, body.service, body.start_time, call_id=call_id)
    return BookingResponse(message=booked_message(booking), booking=BookingOut.from_booking(booking))


def _cancel_booking(service: BookingService, args: dict[str, Any], call_id: str | None) -> BaseModel:
    body = CancelBookingRequest.model_validate(args)
    booking = service.cancel_booking(body.phone, body.start_time)
    return BookingResponse(message=cancelled_message(booking), booking=BookingOut.from_booking(booking))


def _get_current_datetime(service: BookingService, args: dict[str, Any], call_id: str | None) -> BaseModel:
    return get_current_datetime(service)


TOOLS: dict[str, Callable[[BookingService, dict[str, Any], str | None], BaseModel]] = {
    "check_availability": _check_availability,
    "create_booking": _create_booking,
    "cancel_booking": _cancel_booking,
    "get_current_datetime": _get_current_datetime,
}


def _parse_arguments(arguments: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    if not arguments.strip():
        return {}
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        parsed = None
    if not isinstance(parsed, dict):
        raise AppError("invalid_input", "Sorry, I couldn't read the tool arguments.")
    return parsed


def _one_line(payload: BaseModel) -> str:
    """Compact JSON on a single line, as Vapi requires for `result`."""
    return json.dumps(payload.model_dump(mode="json", exclude_none=True), ensure_ascii=False, separators=(",", ":"))


def run_tool_call(service: BookingService, call: VapiToolCall, vapi_call_id: str | None = None) -> VapiToolResult:
    name = call.function.name
    fields = {"tool": name, "tool_call_id": call.id, "vapi_call_id": vapi_call_id}

    handler = TOOLS.get(name)
    if handler is None:
        log_event(logger, logging.WARNING, "vapi_unknown_tool", **fields)
        return VapiToolResult(
            toolCallId=call.id, name=name, error=f"Unknown tool '{name}'. Available: {', '.join(TOOLS)}."
        )

    try:
        payload = handler(service, _parse_arguments(call.function.arguments), vapi_call_id)
        outcome = "ok"
    except AppError as error:
        # A booking rule said no (time taken, closed day, ...): the assistant reads this to the caller.
        payload = ErrorResponse(error=error.code, message=error.message, suggested_times=error.suggested_times)
        outcome = error.code
    except ValidationError as error:
        # The assistant sent a missing or badly formatted argument; the message tells it what to fix.
        bad_fields = sorted({str(e["loc"][-1]) for e in error.errors()})
        payload = ErrorResponse(error="invalid_input", message=invalid_input_message(bad_fields))
        outcome = "invalid_input"
    except Exception:
        logger.exception("vapi_tool_failed", extra={"fields": fields})
        return VapiToolResult(
            toolCallId=call.id,
            name=name,
            error="The booking system had an internal error.",
        )

    log_event(logger, logging.INFO, "vapi_tool_call", outcome=outcome, **fields)
    return VapiToolResult(toolCallId=call.id, name=name, result=_one_line(payload))


# ======================= End-of-call report =======================
# Sent once after every call (https://docs.vapi.ai/server-url/events#end-of-call-report):
#   {"message": {"type": "end-of-call-report",
#                "endedReason": "customer-ended-call",
#                "startedAt": "2026-11-23T08:58:01.000Z", "endedAt": "2026-11-23T09:00:16.000Z",
#                "cost": 0.0634,
#                "artifact": {"transcript": "AI: Hello! ...\nUser: ...", "messages": [...], ...},
#                "analysis": {"summary": "The caller booked a haircut ..."},
#                "call": {"id": "...", "customer": {"number": "+389..."}, ...}}}
# Several fields also exist inside `call` (call.startedAt, call.cost, ...), and older payloads
# put transcript/summary at the top level. We look in all those places, newest format first.


def _field(data: dict[str, Any] | None, *path: str) -> Any:
    """data["a"]["b"]... or None if any step is missing or not an object."""
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _first(*values: Any) -> Any:
    return next((v for v in values if v not in (None, "")), None)


def _parse_time(value: Any) -> datetime | None:
    """'2026-11-23T08:58:01.000Z' -> a Skopje datetime. Anything unreadable -> None."""
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment.astimezone(TIMEZONE) if moment.tzinfo else moment.replace(tzinfo=TIMEZONE)


def _parse_cost(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        # Rounded to 4 decimals, like the database column.
        return round(Decimal(str(value)), 4)
    except InvalidOperation:
        return None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def parse_end_of_call_report(message: dict[str, Any]) -> CallRecord | None:
    """Turn Vapi's report into a CallRecord. None if it has no call id (then we can't store it)."""
    call = message.get("call") if isinstance(message.get("call"), dict) else {}
    call_id = _text(call.get("id"))
    if call_id is None:
        return None

    started = _parse_time(_first(message.get("startedAt"), call.get("startedAt")))
    ended = _parse_time(_first(message.get("endedAt"), call.get("endedAt")))
    duration = _first(message.get("durationSeconds"), call.get("durationSeconds"))
    if isinstance(duration, int | float) and not isinstance(duration, bool) and duration >= 0:
        duration_seconds = round(duration)
    elif started and ended and ended >= started:
        duration_seconds = round((ended - started).total_seconds())
    else:
        duration_seconds = None

    return CallRecord(
        id=call_id,
        started_at=started,
        ended_at=ended,
        duration_seconds=duration_seconds,
        ended_reason=_text(_first(message.get("endedReason"), call.get("endedReason"))),
        transcript=_text(
            _first(
                _field(message, "artifact", "transcript"),
                message.get("transcript"),
                _field(call, "artifact", "transcript"),
            )
        ),
        summary=_text(
            _first(
                _field(message, "analysis", "summary"),
                message.get("summary"),
                _field(call, "analysis", "summary"),
            )
        ),
        cost=_parse_cost(_first(message.get("cost"), call.get("cost"))),
        customer_number=_text(_first(_field(message, "customer", "number"), _field(call, "customer", "number"))),
    )


def save_end_of_call_report(calls: CallService, message: VapiMessage) -> None:
    record = parse_end_of_call_report(message.model_dump())
    if record is None:
        log_event(logger, logging.WARNING, "vapi_report_without_call_id")
        return
    # If saving fails (database down), the error goes up to the middleware, which logs it and
    # answers 500. Vapi only retries reports if a retry plan is set on the Server URL.
    calls.record_call(record)


# ======================= The endpoints =======================

router = APIRouter(prefix="/vapi", tags=["Vapi"], dependencies=[Depends(require_vapi_secret)])

EXAMPLE_REQUEST = {
    "message": {
        "type": "tool-calls",
        "toolCallList": [
            {
                "id": "call_abc123",
                "type": "function",
                "function": {"name": "check_availability", "arguments": {"date": "2026-11-23", "service": "Haircut"}},
            }
        ],
        "call": {"id": "vapi-call-id"},
    }
}


EXAMPLE_REPORT = {
    "message": {
        "type": "end-of-call-report",
        "endedReason": "customer-ended-call",
        "startedAt": "2026-11-23T08:58:01.000Z",
        "endedAt": "2026-11-23T09:00:16.000Z",
        "cost": 0.0634,
        "artifact": {"transcript": "AI: Hello, how can I help?\nUser: I'd like a haircut on Monday."},
        "analysis": {"summary": "The caller booked a haircut for Monday at 14:00."},
        "call": {"id": "vapi-call-id", "customer": {"number": "+38970123456"}},
    }
}

ROUTE_DOCS: dict[str, Any] = {
    "response_model": VapiResponse,
    "response_model_exclude_none": True,  # send `result` or `error`, never both
    "openapi_extra": {
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "tool-calls": {"summary": "A tool call", "value": EXAMPLE_REQUEST},
                        "end-of-call-report": {"summary": "An end-of-call report", "value": EXAMPLE_REPORT},
                    }
                }
            }
        }
    },
    "responses": {
        200: {
            "description": "For tool-calls: one result per tool call (`result` is a single-line JSON "
            "string). For every other message type: `{}`.",
            "content": {
                "application/json": {
                    "example": {
                        "results": [
                            {
                                "toolCallId": "call_abc123",
                                "name": "check_availability",
                                "result": '{"success":true,"date":"2026-11-23","service":"Haircut",'
                                '"duration_minutes":30,"free_times":["09:00","09:30"],'
                                '"message":"On Monday 23 November, a Haircut is free at 09:00 and 09:30."}',
                            }
                        ]
                    }
                }
            },
        },
        401: {"model": ErrorResponse, "description": "Missing or wrong Vapi credential (VAPI_SECRET)."},
    },
}


def handle_message(
    body: VapiWebhook,
    service: BookingService = Depends(get_booking_service),
    calls: CallService = Depends(get_call_service),
) -> VapiResponse:
    """Shared by both URLs, so it doesn't matter which one a Vapi setting points to."""
    message = body.message
    if message.type == "tool-calls":
        vapi_call_id = (message.call or {}).get("id")
        return VapiResponse(results=[run_tool_call(service, call, vapi_call_id) for call in message.toolCallList])
    if message.type == "end-of-call-report":
        save_end_of_call_report(calls, message)
        return VapiResponse()
    # Status updates, speech updates, ...: Vapi needs no answer, and we don't use them.
    log_event(logger, logging.DEBUG, "vapi_event_ignored", message_type=message.type)
    return VapiResponse()


router.add_api_route(
    "/events",
    handle_message,
    methods=["POST"],
    summary="Vapi Server URL (all events)",
    description="Set this URL as the **Server URL of the assistant**. Handles `tool-calls` "
    "(runs each tool and returns one result per call) and `end-of-call-report` (saves the call "
    "to the call history). Other message types are acknowledged with `{}`. "
    "Always answers 200 for valid payloads.",
    **ROUTE_DOCS,
)
router.add_api_route(
    "/tools",
    handle_message,
    methods=["POST"],
    summary="Vapi tool Server URL",
    description="The Server URL for the **tools**. Behaves exactly like `/vapi/events`, so either URL "
    "works in any Vapi setting.",
    **ROUTE_DOCS,
)
