"""
Small helpers that turn data into text for the admin pages ("2 min 15 s", "Mon 23 Nov 14:00", ...).
They are registered as Jinja2 filters in routes.py, e.g. {{ call.duration_seconds | duration }}.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.config import TIMEZONE

# How Vapi labels the two sides of the conversation in a transcript.
SPEAKERS = {"ai": "Assistant", "assistant": "Assistant", "bot": "Assistant", "user": "Caller", "customer": "Caller"}


def when(moment: datetime | None, with_date: bool = True) -> str:
    if moment is None:
        return "—"
    local = moment.astimezone(TIMEZONE)
    return f"{local:%a} {local.day} {local:%b %H:%M}" if with_date else f"{local:%H:%M}"


def duration(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    minutes, rest = divmod(seconds, 60)
    return f"{minutes} min {rest} s" if minutes else f"{rest} s"


def money(amount: Decimal | None) -> str:
    return "—" if amount is None else f"${amount:.2f}"


def ended_reason(reason: str | None) -> str:
    """'customer-ended-call' -> 'Customer ended call'"""
    if not reason:
        return "—"
    text = reason.replace("-", " ").replace("_", " ").replace(".", ": ")
    return text[:1].upper() + text[1:]


@dataclass
class TranscriptLine:
    speaker: str | None  # "Assistant", "Caller", or None for a line we can't attribute
    text: str

    @property
    def side(self) -> str:
        """CSS class: the caller's lines are shown on one side, the assistant's on the other."""
        return {"Assistant": "assistant", "Caller": "caller"}.get(self.speaker or "", "other")


def transcript_lines(transcript: str | None) -> list[TranscriptLine]:
    """
    'AI: Hello!\\nUser: Hi\\nthere' -> [Assistant: 'Hello!', Caller: 'Hi there'].
    A line without a known 'Speaker:' prefix belongs to the line before it.
    """
    lines: list[TranscriptLine] = []
    for raw in (transcript or "").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        prefix, colon, rest = raw.partition(":")
        speaker = SPEAKERS.get(prefix.strip().lower()) if colon else None
        if speaker:
            lines.append(TranscriptLine(speaker, rest.strip()))
        elif lines:
            lines[-1].text += " " + raw
        else:
            lines.append(TranscriptLine(None, raw))
    return lines


def percent(part: int, whole: int) -> str:
    return "—" if whole == 0 else f"{round(100 * part / whole)}%"
