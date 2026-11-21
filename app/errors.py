"""
The one error type the API turns into a friendly JSON answer.

Raise `AppError` (or a subclass like BookingError) anywhere, and main.py
converts it into {"success": false, "error": ..., "message": ...}.
"""


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
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        # Alternative start times (e.g. ["13:30", "14:30"]) so the agent can offer them directly.
        self.suggested_times = suggested_times or []
