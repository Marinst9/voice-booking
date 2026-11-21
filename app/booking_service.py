"""
The booking rules.

This file decides what is allowed (opening hours, no double bookings, ...)
and writes the sentences the voice agent reads back to the caller.
It does not know or care which calendar is used underneath.
"""

import logging
import threading
import uuid
from collections.abc import Callable
from datetime import date, datetime, timedelta

from app.calendars import Booking, CalendarBackend
from app.config import (
    OPENING_HOURS,
    SERVICES,
    SLOT_STEP_MINUTES,
    TIMEZONE,
    Service,
    find_service,
)
from app.errors import AppError
from app.logging_config import log_event, mask_phone

logger = logging.getLogger("app.booking")


class BookingError(AppError):
    """A booking rule was broken (closed day, time taken, ...). See app/errors.py."""


# ---------- small helpers for nice spoken text ----------

def say_time(moment: datetime) -> str:
    """09:30"""
    return moment.strftime("%H:%M")


def say_day(day: date) -> str:
    """Monday 23 November"""
    return f"{day:%A} {day.day} {day:%B}"


def say_list(items: list[str]) -> str:
    """['a'] -> 'a', ['a', 'b'] -> 'a and b', ['a', 'b', 'c'] -> 'a, b and c'"""
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def opening_hours_sentence() -> str:
    """'We're open Monday to Saturday, 09:00 to 18:00.' built from config.py."""
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    open_days = sorted(OPENING_HOURS)
    first_open, first_close = OPENING_HOURS[open_days[0]]
    return (
        f"We're open {day_names[open_days[0]]} to {day_names[open_days[-1]]}, "
        f"{first_open:%H:%M} to {first_close:%H:%M}."
    )


def normalize_phone(phone: str) -> str:
    """'+389 70 123-456' -> '+38970123456', so the same number always matches."""
    digits = "".join(ch for ch in phone if ch.isdigit())
    return ("+" + digits) if phone.strip().startswith("+") else digits


def to_shop_time(moment: datetime) -> datetime:
    """
    Make sure a datetime is in Skopje time.
    A time without a timezone ("2026-11-23T14:00") is treated as Skopje time.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=TIMEZONE)
    return moment.astimezone(TIMEZONE)


# ---------- the service itself ----------

class BookingService:
    def __init__(
        self,
        calendar: CalendarBackend,
        now: Callable[[], datetime] = lambda: datetime.now(TIMEZONE),
    ) -> None:
        # `now` is a function so tests can pretend it's a specific day and time.
        self.calendar = calendar
        self.now = now
        # Prevents two callers from grabbing the same slot at the exact same moment.
        self._lock = threading.Lock()

    # ----- lookups -----

    def get_service(self, text: str) -> Service:
        service = find_service(text)
        if service is None:
            names = say_list([s.name for s in SERVICES])
            raise BookingError(
                "unknown_service",
                f"Sorry, we don't offer '{text}'. We offer {names}.",
            )
        return service

    def opening_window(self, day: date) -> tuple[datetime, datetime] | None:
        """Opening and closing time for a day, or None if the shop is closed."""
        hours = OPENING_HOURS.get(day.weekday())
        if hours is None:
            return None
        opens, closes = hours
        return (
            datetime.combine(day, opens, tzinfo=TIMEZONE),
            datetime.combine(day, closes, tzinfo=TIMEZONE),
        )

    def free_start_times(self, day: date, service: Service) -> list[datetime]:
        """Every start time on `day` where `service` fits without overlapping anything."""
        window = self.opening_window(day)
        if window is None:
            return []
        opens, closes = window
        duration = timedelta(minutes=service.duration_minutes)
        step = timedelta(minutes=SLOT_STEP_MINUTES)
        existing = self.calendar.list_bookings(opens, closes)
        now = self.now()

        free = []
        start = opens
        while start + duration <= closes:
            end = start + duration
            in_future = start > now
            clashes = any(b.start < end and start < b.end for b in existing)
            if in_future and not clashes:
                free.append(start)
            start += step
        return free

    def nearest_free_times(self, around: datetime, service: Service, count: int = 2) -> list[datetime]:
        """The `count` free start times closest to `around` on the same day, in time order."""
        free = self.free_start_times(around.date(), service)
        closest = sorted(free, key=lambda t: abs(t - around))[:count]
        return sorted(closest)

    def _suggestion(self, around: datetime, service: Service) -> tuple[str, list[str]]:
        """
        Returns a sentence plus the times in it, e.g.
        ('The nearest free times are 13:30 and 14:30.', ['13:30', '14:30'])
        """
        times = [say_time(t) for t in self.nearest_free_times(around, service)]
        if not times:
            sentence = f"There are no free times left on {say_day(around.date())}, please try another day."
        elif len(times) == 1:
            sentence = f"The nearest free time is {times[0]}."
        else:
            sentence = f"The nearest free times are {say_list(times)}."
        return sentence, times

    def _check_day_is_bookable(self, day: date) -> None:
        if day < self.now().date():
            raise BookingError("date_in_past", f"Sorry, {say_day(day)} has already passed.")
        if self.opening_window(day) is None:
            raise BookingError(
                "closed_day",
                f"Sorry, we're closed on {day:%A}s. {opening_hours_sentence()}",
            )

    # ----- the three actions the voice agent uses -----

    def check_availability(self, day: date, service_text: str) -> dict:
        service = self.get_service(service_text)
        self._check_day_is_bookable(day)
        free = self.free_start_times(day, service)
        times = [say_time(t) for t in free]

        if not times:
            message = f"Sorry, there are no free times for a {service.name} on {say_day(day)}."
        elif len(times) <= 4:
            message = f"On {say_day(day)}, a {service.name} is free at {say_list(times)}."
        else:
            # Don't read 15 times out loud: give a count and the first few.
            message = (
                f"On {say_day(day)} there are {len(times)} free times for a {service.name}. "
                f"The earliest are {say_list(times[:3])}, and the latest is {times[-1]}."
            )
        return {
            "date": day.isoformat(),
            "service": service.name,
            "duration_minutes": service.duration_minutes,
            "free_times": times,
            "message": message,
        }

    def create_booking(self, customer_name: str, phone: str, service_text: str, start: datetime) -> Booking:
        service = self.get_service(service_text)
        start = to_shop_time(start)
        end = start + timedelta(minutes=service.duration_minutes)
        clean_phone = normalize_phone(phone)

        if len(clean_phone.lstrip("+")) < 6:
            raise BookingError("invalid_phone", "Sorry, that phone number doesn't look right. Could you repeat it?")
        if not customer_name.strip():
            raise BookingError("invalid_name", "Sorry, I didn't catch your name. Could you tell me again?")

        self._check_day_is_bookable(start.date())
        if start <= self.now():
            sentence, times = self._suggestion(start, service)
            raise BookingError(
                "time_in_past",
                f"Sorry, {say_time(start)} has already passed today. {sentence}",
                suggested_times=times,
            )

        opens, closes = self.opening_window(start.date())
        if start < opens or end > closes:
            sentence, times = self._suggestion(start, service)
            raise BookingError(
                "outside_hours",
                f"Sorry, a {service.name} at {say_time(start)} doesn't fit in our opening hours. "
                f"On {start:%A}s we're open {say_time(opens)} to {say_time(closes)}, "
                f"and a {service.name} takes {service.duration_minutes} minutes. {sentence}",
                suggested_times=times,
            )

        # The lock makes "check if free" + "save" one step, so no double booking can sneak in.
        with self._lock:
            if self.calendar.list_bookings(start, end):
                sentence, times = self._suggestion(start, service)
                raise BookingError(
                    "time_taken",
                    f"Sorry, that time is already taken. {sentence}",
                    status_code=409,
                    suggested_times=times,
                )
            booking = Booking(
                id=uuid.uuid4().hex[:8],
                customer_name=customer_name.strip(),
                phone=clean_phone,
                service_key=service.key,
                service_name=service.name,
                start=start,
                end=end,
            )
            self.calendar.add_booking(booking)
        log_event(
            logger, logging.INFO, "booking_created",
            booking_id=booking.id, service=booking.service_key,
            start=booking.start.isoformat(), phone=mask_phone(booking.phone),
        )
        return booking

    def cancel_booking(self, phone: str, start: datetime) -> Booking:
        start = to_shop_time(start)
        clean_phone = normalize_phone(phone)
        with self._lock:
            for booking in self.calendar.list_bookings():
                if booking.phone == clean_phone and booking.start == start:
                    self.calendar.remove_booking(booking.id)
                    log_event(
                        logger, logging.INFO, "booking_cancelled",
                        booking_id=booking.id, service=booking.service_key,
                        start=booking.start.isoformat(), phone=mask_phone(booking.phone),
                    )
                    return booking
        raise BookingError(
            "booking_not_found",
            f"Sorry, I couldn't find a booking for that phone number at {say_time(start)} on {say_day(start.date())}.",
            status_code=404,
        )

    def all_bookings(self) -> list[Booking]:
        return self.calendar.list_bookings()
