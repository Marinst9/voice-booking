"""
The one error type the API turns into a friendly JSON answer.

Raise `AppError` (or a subclass like BookingError) anywhere, and main.py
converts it into {"success": false, "error": ..., "message": ...}.
"""


def invalid_input_message(fields: list[str]) -> str:
    """The sentence used when a request is missing a field or has a badly formatted one."""
    return (
        f"Sorry, something was missing or unclear: {', '.join(fields)}. "
        "Dates must look like 2026-11-23 and times like 2026-11-23T14:00."
    )


class AppError(Exception):
    """
    A problem we want to explain to the caller.

    `message` is a friendly sentence the voice agent can read out loud.
    `code` is a short machine-readable label (handy for the AI agent and for tests).
    """

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        suggested_times: list[str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        # Alternative start times (e.g. ["13:30", "14:30"]) so the agent can offer them directly.
        self.suggested_times = suggested_times or []
        # Extra HTTP response headers, e.g. Retry-After on 429 Too Many Requests.
        self.headers = headers or {}
