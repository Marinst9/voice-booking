"""
Rate limiting: "at most N requests per time window, per client".

Used for:
- the REST endpoints and the Vapi webhook (RATE_LIMIT_API_PER_MINUTE, RATE_LIMIT_VAPI_PER_MINUTE),
  so one misbehaving client can't flood the server;
- the admin login (LOGIN_MAX_FAILURES per LOGIN_LOCKOUT_MINUTES), so nobody can guess the
  password by trying thousands of them.

How it works ("sliding window"): for each client we remember the times of its recent
requests. A new request is allowed if fewer than N of them happened in the last window.

The counts live in this process's memory. That's right for this app, which runs as ONE
server process (one Railway replica). With several replicas each would count separately,
and you'd move the counts to a shared store such as Redis.
"""

import logging
import math
import threading
import time
from collections import deque
from collections.abc import Callable

from fastapi import Depends, Request

from app.config import Settings, get_settings
from app.errors import AppError
from app.logging_config import log_event

logger = logging.getLogger("app.rate_limit")

# Forget clients we haven't seen for a while, so memory use stays small.
MAX_TRACKED_CLIENTS = 10_000


class SlidingWindowLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock  # a function, so tests can move time forward without waiting
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, window: float, now: float) -> deque[float]:
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - window:
            hits.popleft()
        return hits

    def retry_after(self, key: str, limit: int, window: float) -> float | None:
        """Seconds until `key` may try again, or None if it's under the limit. Doesn't count anything."""
        if limit <= 0:
            return None
        with self._lock:
            now = self._clock()
            hits = self._recent(key, window, now)
            if len(hits) < limit:
                return None
            return max(hits[0] + window - now, 0.0)

    def hit(self, key: str, limit: int, window: float) -> float | None:
        """
        Count one request. Returns None if it's allowed, otherwise the seconds until the next one is.
        A rejected request is not counted, so a client that keeps hammering isn't locked out forever.
        """
        if limit <= 0:
            return None
        with self._lock:
            now = self._clock()
            hits = self._recent(key, window, now)
            if len(hits) >= limit:
                return max(hits[0] + window - now, 0.0)
            hits.append(now)
            if len(self._hits) > MAX_TRACKED_CLIENTS:
                self._forget_idle(window, now)
            return None

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)

    def _forget_idle(self, window: float, now: float) -> None:
        for key in [k for k, hits in self._hits.items() if not hits or hits[-1] <= now - window]:
            del self._hits[key]


class RateLimiters:
    """The separate counters the app uses. One set per app, created at startup (tests make fresh ones)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.api = SlidingWindowLimiter(clock)
        self.vapi = SlidingWindowLimiter(clock)
        self.login_failures = SlidingWindowLimiter(clock)


def client_ip(request: Request, settings: Settings) -> str:
    """
    The caller's IP address. Behind a proxy (Railway), it comes from the header the proxy sets
    (CLIENT_IP_HEADER, e.g. X-Real-IP), because the connection itself comes from the proxy.
    """
    if settings.client_ip_header:
        value = request.headers.get(settings.client_ip_header, "")
        # If the header holds a list ("client, proxy1, proxy2"), the first entry is the client.
        first = value.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def get_rate_limiters(request: Request) -> RateLimiters:
    return request.app.state.rate_limiters


def _too_many_requests(request: Request, ip: str, wait: float, limiter_name: str) -> AppError:
    seconds = max(math.ceil(wait), 1)
    log_event(logger, logging.WARNING, "rate_limited", limiter=limiter_name, path=request.url.path, client_ip=ip)
    return AppError(
        "rate_limited",
        f"Too many requests. Please wait {seconds} seconds and try again.",
        status_code=429,
        headers={"Retry-After": str(seconds)},  # tells well-behaved clients how long to wait
    )


def limit_api(
    request: Request,
    settings: Settings = Depends(get_settings),
    limiters: RateLimiters = Depends(get_rate_limiters),
) -> None:
    """Dependency for the REST endpoints: RATE_LIMIT_API_PER_MINUTE per client IP."""
    ip = client_ip(request, settings)
    wait = limiters.api.hit(ip, settings.rate_limit_api_per_minute, 60)
    if wait is not None:
        raise _too_many_requests(request, ip, wait, "api")


def limit_vapi(
    request: Request,
    settings: Settings = Depends(get_settings),
    limiters: RateLimiters = Depends(get_rate_limiters),
) -> None:
    """Dependency for /vapi/...: RATE_LIMIT_VAPI_PER_MINUTE per client IP."""
    ip = client_ip(request, settings)
    wait = limiters.vapi.hit(ip, settings.rate_limit_vapi_per_minute, 60)
    if wait is not None:
        raise _too_many_requests(request, ip, wait, "vapi")
