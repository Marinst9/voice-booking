"""
Vapi adapter: lets a Vapi voice assistant use the booking service as "tools".

How it works (https://docs.vapi.ai/tools/custom-tools):

1. During a call, the assistant decides to use a tool. Vapi POSTs to /vapi/tools:
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

This file only translates formats. All booking rules stay in app/booking_service.py.
"""

import json
import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.booking_service import BookingService, booked_message, cancelled_message, say_day, say_time
from app.config import settings
from app.dependencies import get_booking_service
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


class VapiWebhook(BaseModel):
    message: VapiMessage


# ======================= What we send back =======================


class VapiToolResult(BaseModel):
    toolCallId: str
    name: str
    result: str | None = Field(None, description="Single-line JSON string the assistant reads")
    error: str | None = Field(None, description="Only for real failures (unknown tool, server error)")


class VapiToolResponse(BaseModel):
    results: list[VapiToolResult]


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
# Each tool: (booking service, arguments dict) -> a response model.
# The REST endpoints and these tools return the exact same JSON shapes.


def _check_availability(service: BookingService, args: dict[str, Any]) -> BaseModel:
    body = CheckAvailabilityRequest.model_validate(args)
    return AvailabilityResponse(**service.check_availability(body.date, body.service))


def _create_booking(service: BookingService, args: dict[str, Any]) -> BaseModel:
    body = CreateBookingRequest.model_validate(args)
    booking = service.create_booking(body.customer_name, body.phone, body.service, body.start_time)
    return BookingResponse(message=booked_message(booking), booking=BookingOut.from_booking(booking))


def _cancel_booking(service: BookingService, args: dict[str, Any]) -> BaseModel:
    body = CancelBookingRequest.model_validate(args)
    booking = service.cancel_booking(body.phone, body.start_time)
    return BookingResponse(message=cancelled_message(booking), booking=BookingOut.from_booking(booking))


def _get_current_datetime(service: BookingService, args: dict[str, Any]) -> BaseModel:
    return get_current_datetime(service)


TOOLS: dict[str, Callable[[BookingService, dict[str, Any]], BaseModel]] = {
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
        payload = handler(service, _parse_arguments(call.function.arguments))
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


# ======================= The endpoint =======================

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


@router.post(
    "/tools",
    response_model=VapiToolResponse,
    response_model_exclude_none=True,  # send `result` or `error`, never both
    summary="Vapi tool-call webhook",
    description="Set this URL as the **Server URL** of the Vapi tools. Runs each tool call in "
    "`message.toolCallList` and returns one result per call. Always answers 200 for valid "
    "payloads. Other Vapi message types are acknowledged with an empty result list.",
    openapi_extra={"requestBody": {"content": {"application/json": {"example": EXAMPLE_REQUEST}}}},
    responses={
        200: {
            "description": "One result per tool call. `result` is a single-line JSON string.",
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
)
def handle_tool_calls(body: VapiWebhook, service: BookingService = Depends(get_booking_service)) -> VapiToolResponse:
    message = body.message
    if message.type != "tool-calls":
        # If the assistant's Server URL also points here, Vapi sends other events (status updates,
        # end-of-call reports, ...). We don't use them yet, so just acknowledge.
        log_event(logger, logging.DEBUG, "vapi_event_ignored", message_type=message.type)
        return VapiToolResponse(results=[])

    vapi_call_id = (message.call or {}).get("id")
    return VapiToolResponse(results=[run_tool_call(service, call, vapi_call_id) for call in message.toolCallList])
