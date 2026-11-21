"""
What the requests and responses look like (the "shape" of the JSON).

FastAPI uses these classes to check incoming data automatically and to
build the interactive docs page at /docs. The examples show up there.
"""

import datetime as dt
from datetime import datetime

from pydantic import BaseModel, Field


class CheckAvailabilityRequest(BaseModel):
    # The type is written as dt.date because the field itself is called "date".
    date: dt.date = Field(description="Day to check, format YYYY-MM-DD", examples=["2026-11-23"])
    service: str = Field(description="Haircut, Beard trim or Haircut + Beard", examples=["Haircut"])


class CreateBookingRequest(BaseModel):
    customer_name: str = Field(min_length=1, examples=["Marko"])
    phone: str = Field(min_length=1, examples=["+389 70 123 456"])
    service: str = Field(examples=["Haircut + Beard"])
    start_time: datetime = Field(
        description="Start in Skopje time, format YYYY-MM-DDTHH:MM",
        examples=["2026-11-23T14:00"],
    )


class CancelBookingRequest(BaseModel):
    phone: str = Field(min_length=1, examples=["+389 70 123 456"])
    start_time: datetime = Field(examples=["2026-11-23T14:00"])


class BookingOut(BaseModel):
    id: str
    customer_name: str
    phone: str
    service: str
    start_time: datetime
    end_time: datetime
