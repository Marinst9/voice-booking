"""
Structured logging.

Instead of free text, every log line is an *event name* plus *fields*:

    log_event(logger, logging.INFO, "booking_created", booking_id="ab12", service="Haircut")

With LOG_FORMAT=json (default) that prints one JSON object per line, which
log tools (Docker, Grafana, Datadog, ...) can search and filter:

    {"time": "...", "level": "INFO", "logger": "app.booking", "event": "booking_created",
     "request_id": "9f2c...", "booking_id": "ab12", "service": "Haircut"}

With LOG_FORMAT=text you get a shorter, human-friendly line for local work.
"""

import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

# The id of the HTTP request currently being handled, added to every log line
# automatically so you can find all lines that belong to one call.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    """Log an event with extra fields. Fields go in `extra` so they don't clash with built-in names."""
    logger.log(level, event, extra={"fields": fields})


def mask_phone(phone: str) -> str:
    """Phone numbers are personal data: only log the last 3 digits."""
    return "***" + phone[-3:] if len(phone) > 3 else "***"


def _record_to_dict(record: logging.LogRecord) -> dict[str, Any]:
    data: dict[str, Any] = {
        "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
        "level": record.levelname,
        "logger": record.name,
        "event": record.getMessage(),
    }
    request_id = request_id_var.get()
    if request_id:
        data["request_id"] = request_id
    data.update(getattr(record, "fields", {}))
    if record.exc_info:
        data["exception"] = logging.Formatter().formatException(record.exc_info)
    return data


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(_record_to_dict(record), default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = _record_to_dict(record)
        head = f"{data.pop('time')} {data.pop('level'):<7} {data.pop('event')}"
        data.pop("logger")
        exception = data.pop("exception", None)
        line = head + "".join(f" {key}={value}" for key, value in data.items())
        return f"{line}\n{exception}" if exception else line


def setup_logging(level: str = "INFO", log_format: str = "json") -> None:
    """Send all logs to the console in the chosen format. Safe to call more than once."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if log_format == "json" else TextFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # Uvicorn's own access log would duplicate our request log.
    logging.getLogger("uvicorn.access").disabled = True
