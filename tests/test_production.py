"""
Tests for the production hardening: production settings, /health, /docs on/off,
secure cookies, HSTS, and rate limiting (API, Vapi webhook, admin login).
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.main import create_app
from app.rate_limit import RateLimiters, SlidingWindowLimiter, client_ip
from tests.conftest import TEST_ADMIN_PASSWORD, TEST_ADMIN_USERNAME, TEST_API_KEY, TEST_VAPI_SECRET
from tests.test_admin import csrf_from, log_in

PROD = {
    "environment": "production",
    "api_key": TEST_API_KEY,
    "calendar_backend": "postgres",
    "database_url": "postgresql://user:pw@db.internal:5432/railway",
}


def prod_settings(**changes) -> Settings:
    return Settings(_env_file=None, **{**PROD, **changes})


class FakeClock:
    """Time that only moves when the test says so."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# ---------- Settings ----------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("postgresql://u:p@host:5432/db", "postgresql+psycopg://u:p@host:5432/db"),  # Railway
        ("postgres://u:p@host:5432/db", "postgresql+psycopg://u:p@host:5432/db"),  # Heroku style
        ("postgresql+psycopg://u:p@host/db", "postgresql+psycopg://u:p@host/db"),  # already right
    ],
)
def test_platform_database_urls_get_the_psycopg_driver(given, expected):
    assert Settings(_env_file=None, database_url=given).database_url == expected


def test_production_defaults():
    s = prod_settings()
    assert s.is_production
    assert s.show_docs is False
    assert s.secure_cookies is True


def test_development_defaults():
    s = Settings(_env_file=None)
    assert s.show_docs is True
    assert s.secure_cookies is False


def test_docs_can_be_turned_on_in_production():
    assert prod_settings(docs_enabled=True).show_docs is True


@pytest.mark.parametrize(
    ("changes", "complaint"),
    [
        ({"api_key": None}, "API_KEY must be set"),
        ({"calendar_backend": "memory"}, "CALENDAR_BACKEND must be postgres"),
        ({"admin_password": TEST_ADMIN_PASSWORD}, "SESSION_SECRET must be set"),
        ({"session_cookie_secure": False}, "SESSION_COOKIE_SECURE can't be false"),
    ],
)
def test_unsafe_production_settings_are_refused(changes, complaint):
    with pytest.raises(ValidationError, match=complaint):
        prod_settings(**changes)


# ---------- A production app: no docs, HSTS, secure cookie ----------


@pytest.fixture
def prod_client(booking_service):
    config = prod_settings(
        admin_username=TEST_ADMIN_USERNAME,
        admin_password=TEST_ADMIN_PASSWORD,
        session_secret="x" * 40,
        vapi_secret=TEST_VAPI_SECRET,
    )
    prod_app = create_app(config)
    prod_app.dependency_overrides[get_settings] = lambda: config
    prod_app.state.booking_service = booking_service  # what the startup would do
    # base_url https: like on Railway, where the platform serves the app over HTTPS.
    return TestClient(prod_app, base_url="https://testserver", headers={"X-API-Key": TEST_API_KEY})


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_docs_are_off_in_production(prod_client, path):
    assert prod_client.get(path).status_code == 404


def test_docs_are_on_in_development(client):
    assert client.get("/docs").status_code == 200


def test_production_sends_hsts(prod_client):
    assert prod_client.get("/health").headers["strict-transport-security"] == "max-age=31536000"


def test_development_sends_no_hsts(client):
    assert "strict-transport-security" not in client.get("/health").headers


def test_production_login_cookie_is_https_only(prod_client):
    cookie = prod_client.get("/admin/login").headers["set-cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie and "samesite=lax" in cookie


def test_development_login_cookie_also_works_over_http(client):
    cookie = client.get("/admin/login").headers["set-cookie"].lower()
    assert "secure" not in cookie and "httponly" in cookie


def test_the_api_still_works_in_production(prod_client):
    response = prod_client.post("/check_availability", json={"date": "2026-11-23", "service": "Haircut"})
    assert response.status_code == 200


# ---------- /health ----------


def test_health_without_api_key(client):
    response = client.get("/health", headers={"X-API-Key": ""})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_reports_the_database(client, test_settings, backend):
    test_settings.calendar_backend = backend  # "memory" or "postgres", like the calendar in this test run
    expected = "ok" if backend == "postgres" else "not_used"
    assert client.get("/health").json() == {"status": "ok", "database": expected}


def test_health_is_503_when_the_database_is_down(client, booking_service, monkeypatch):
    def unreachable():
        raise ConnectionError("connection refused to db.internal:5432")

    monkeypatch.setattr(booking_service, "check_health", unreachable)
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json() == {"status": "error", "database": "unreachable"}
    assert "db.internal" not in response.text  # details only in the logs


# ---------- The limiter itself ----------


def test_limiter_allows_up_to_the_limit_then_blocks():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(clock)
    assert [limiter.hit("a", 3, 60) for _ in range(3)] == [None, None, None]
    assert limiter.hit("a", 3, 60) == 60  # must wait for the oldest hit to leave the window
    assert limiter.hit("b", 3, 60) is None  # other clients aren't affected


def test_limiter_window_slides():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(clock)
    limiter.hit("a", 2, 60)
    clock.now += 30
    limiter.hit("a", 2, 60)
    assert limiter.hit("a", 2, 60) == 30  # the first hit leaves the window in 30 s
    clock.now += 30
    assert limiter.hit("a", 2, 60) is None


def test_rejected_requests_are_not_counted():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(clock)
    limiter.hit("a", 1, 60)
    for _ in range(100):
        limiter.hit("a", 1, 60)  # hammering while blocked...
    clock.now += 60
    assert limiter.hit("a", 1, 60) is None  # ...doesn't extend the block


def test_retry_after_does_not_count_and_reset_clears():
    limiter = SlidingWindowLimiter(FakeClock())
    assert limiter.retry_after("a", 1, 60) is None
    limiter.hit("a", 1, 60)
    assert limiter.retry_after("a", 1, 60) == 60
    limiter.reset("a")
    assert limiter.retry_after("a", 1, 60) is None


def test_limit_zero_means_no_limit():
    limiter = SlidingWindowLimiter(FakeClock())
    assert all(limiter.hit("a", 0, 60) is None for _ in range(1000))


class FakeRequest:
    def __init__(self, headers: dict, host: str = "10.0.0.5") -> None:
        self.headers = headers
        self.client = type("Client", (), {"host": host})()


def test_client_ip_from_the_connection_by_default():
    request = FakeRequest({"X-Real-IP": "6.6.6.6"})
    assert client_ip(request, Settings(_env_file=None)) == "10.0.0.5"  # the header is NOT trusted


def test_client_ip_from_the_proxy_header_when_configured():
    settings = Settings(_env_file=None, client_ip_header="X-Real-IP")
    assert client_ip(FakeRequest({"X-Real-IP": "203.0.113.7"}), settings) == "203.0.113.7"
    assert client_ip(FakeRequest({"X-Real-IP": "203.0.113.7, 10.1.1.1"}), settings) == "203.0.113.7"
    assert client_ip(FakeRequest({}), settings) == "10.0.0.5"  # header missing: fall back


# ---------- Rate limits on the endpoints ----------


def test_api_rate_limit(client, test_settings):
    test_settings.rate_limit_api_per_minute = 3
    for _ in range(3):
        assert client.get("/bookings").status_code == 200
    response = client.get("/bookings")
    assert response.status_code == 429
    assert response.json()["error"] == "rate_limited"
    assert "Please wait 60 seconds" in response.json()["message"]
    assert response.headers["retry-after"] == "60"


def test_api_rate_limit_also_counts_wrong_keys(client, test_settings):
    test_settings.rate_limit_api_per_minute = 2
    bad = {"X-API-Key": "wrong-key-wrong-key"}
    assert [client.get("/bookings", headers=bad).status_code for _ in range(3)] == [401, 401, 429]


def test_api_rate_limit_is_per_client_behind_a_proxy(client, test_settings):
    test_settings.rate_limit_api_per_minute = 1
    test_settings.client_ip_header = "X-Real-IP"
    assert client.get("/bookings", headers={"X-Real-IP": "1.1.1.1"}).status_code == 200
    assert client.get("/bookings", headers={"X-Real-IP": "1.1.1.1"}).status_code == 429
    assert client.get("/bookings", headers={"X-Real-IP": "2.2.2.2"}).status_code == 200


def test_faked_ip_header_is_ignored_without_a_proxy(client, test_settings):
    test_settings.rate_limit_api_per_minute = 1
    client.get("/bookings", headers={"X-Real-IP": "1.1.1.1"})
    assert client.get("/bookings", headers={"X-Real-IP": "9.9.9.9"}).status_code == 429


def test_health_is_not_rate_limited(client, test_settings):
    test_settings.rate_limit_api_per_minute = 1
    assert all(client.get("/health").status_code == 200 for _ in range(5))


def test_vapi_rate_limit(client, test_settings):
    test_settings.rate_limit_vapi_per_minute = 2
    payload = {"message": {"type": "status-update"}}
    headers = {"Authorization": f"Bearer {TEST_VAPI_SECRET}"}
    codes = [client.post("/vapi/events", json=payload, headers=headers).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


# ---------- Admin login lockout ----------


@pytest.fixture
def clock(client):
    """Make the app's rate limiters use a fake clock."""
    fake = FakeClock()
    client.app.state.rate_limiters = RateLimiters(clock=fake)
    return fake


def test_login_locks_after_too_many_failures(client, test_settings, clock):
    test_settings.login_max_failures = 3
    for _ in range(3):
        assert log_in(client, password="wrong-password-123").status_code == 401

    # Now even the right password is refused (so guessing can't continue)...
    response = log_in(client)
    assert response.status_code == 429
    assert "Too many failed logins. Try again in 15 minutes." in response.text
    assert response.headers["retry-after"] == "900"

    # ...until the lockout has passed.
    clock.now += 15 * 60
    assert log_in(client).status_code == 303


def test_correct_login_clears_earlier_failures(client, test_settings, clock):
    test_settings.login_max_failures = 3
    log_in(client, password="wrong-password-123")
    log_in(client, password="wrong-password-123")
    assert log_in(client).status_code == 303
    token = csrf_from(client.get("/admin").text)
    client.post("/admin/logout", data={"csrf": token})
    # Two more failures don't lock: the counter started again from zero.
    assert [log_in(client, password="nope-nope-nope").status_code for _ in range(2)] == [401, 401]
    assert log_in(client).status_code == 303


def test_login_lockout_is_per_ip(client, test_settings, clock):
    test_settings.login_max_failures = 1
    test_settings.client_ip_header = "X-Real-IP"
    client.headers["X-Real-IP"] = "6.6.6.6"
    log_in(client, password="wrong-password-123")
    assert log_in(client).status_code == 429
    client.headers["X-Real-IP"] = "203.0.113.7"  # the shop owner, somewhere else
    assert log_in(client).status_code == 303
