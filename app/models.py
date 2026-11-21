"""
What the requests and responses look like (the "shape" of the JSON).

FastAPI uses these classes to:
  - check incoming data automatically,
  - filter outgoing data to exactly these fields,
  - build the interactive docs page at /docs (the examples show up there).
"""

import datetime as dt
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# ======================= Requests (what the agent sends) =======================


class CheckAvailabilityRequest(BaseModel):
    # The type is written as dt.date because the field itself is called "date".
    date: dt.date = Field(description="Day to check, format YYYY-MM-DD", examples=["2026-11-23"])
    service: str = Field(description="Haircut, Beard trim or Haircut + Beard", examples=["Haircut"])


class CreateBookingRequest(BaseModel):
    customer_name: str = Field(min_length=1, examples=["Marko"])
    phone: str = Field(min_length=1, examples=["+389 70 123 456"])
    service: str = Field(description="Haircut, Beard trim or Haircut + Beard", examples=["Haircut + Beard"])
    start_time: datetime = Field(
        description="Start in Skopje time, format YYYY-MM-DDTHH:MM",
        examples=["2026-11-23T14:00"],
    )


class CancelBookingRequest(BaseModel):
    phone: str = Field(min_length=1, description="The phone number used when booking", examples=["+389 70 123 456"])
    start_time: datetime = Field(description="Start time of the booking to cancel", examples=["2026-11-23T14:00"])


# ======================= Responses (what we send back) =======================


class BookingOut(BaseModel):
    """One booking, as shown to the agent and on /bookings."""

    id: str = Field(examples=["3e9aee92"])
    customer_name: str = Field(examples=["Marko"])
    phone: str = Field(description="Normalized: digits only, with a leading + if given", examples=["+38970123456"])
    service: str = Field(examples=["Haircut"])
    start_time: datetime = Field(examples=["2026-11-23T14:00:00+01:00"])
    end_time: datetime = Field(examples=["2026-11-23T14:30:00+01:00"])


class AvailabilityResponse(BaseModel):
    success: Literal[True] = True
    date: dt.date = Field(examples=["2026-11-23"])
    service: str = Field(examples=["Haircut"])
    duration_minutes: int = Field(examples=[30])
    free_times: list[str] = Field(description="Free start times, HH:MM", examples=[["09:00", "09:30", "10:00"]])
    message: str = Field(
        description="Sentence to read to the caller",
        examples=["On Monday 23 November there are 18 free times for a Haircut. "
                  "The earliest are 09:00, 09:30 and 10:00, and the latest is 17:30."],
    )


class BookingResponse(BaseModel):
    """Returned after a booking is created or cancelled."""

    success: Literal[True] = True
    message: str = Field(
        description="Sentence to read to the caller",
        examples=["You're booked, Marko! Haircut on Monday 23 November at 14:00."],
    )
    booking: BookingOut


class BookingListResponse(BaseModel):
    success: Literal[True] = True
    count: int = Field(examples=[1])
    bookings: list[BookingOut]


class ErrorResponse(BaseModel):
    """Every error has this shape, so the agent can always read `message` out loud."""

    success: Literal[False] = False
    error: str = Field(
        description="Machine-readable code, e.g. time_taken, closed_day, outside_hours",
        examples=["time_taken"],
    )
    message: str = Field(
        description="Sentence to read to the caller",
        examples=["Sorry, that time is already taken. The nearest free times are 13:30 and 14:30."],
    )
    suggested_times: list[str] = Field(
        default_factory=list,
        description="Nearby free start times (HH:MM) the agent can offer instead. Empty if none apply.",
        examples=[["13:30", "14:30"]],
    )
    details: list[dict[str, Any]] | None = Field(
        default=None,
        description="Only for invalid_input: the technical validation errors.",
    )
