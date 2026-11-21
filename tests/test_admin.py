"""
Tests for the admin dashboard (/admin): login, the pages, and the cancel button.

Fake "now" is Sunday 22 November 2026, 12:00 (Skopje), so "this week" is Mon 16 - Sun 22 November.
Like the API tests, these run against both the in-memory storage and PostgreSQL.
"""

import re
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.admin import views
from app.calendars import Booking, BookingStatus
from app.calls import CallRecord
from app.config import TIMEZONE
from tests.conftest import TEST_ADMIN_PASSWORD, TEST_ADMIN_USERNAME

TODAY_15 = datetime(2026, 11, 22, 15, 0, tzinfo=TIMEZONE)  # Sunday (closed, but the calendar itself allows it)
MONDAY_14 = datetime(2026, 11, 23, 14, 0, tzinfo=TIMEZONE)


# ---------- Helpers ----------


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "page has no CSRF token"
    return match.group(1)


def log_in(client, username=TEST_ADMIN_USERNAME, password=TEST_ADMIN_PASSWORD, next_path="/admin"):
    token = csrf_from(client.get("/admin/login").text)
    return client.post(
        "/admin/login",
        data={"username": username, "password": password, "next": next_path, "csrf": token},
        follow_redirects=False,
    )


@pytest.fixture
def admin(client):
    """The test client, logged in to the dashboard."""
    assert log_in(client).status_code == 303
    return client


def add_booking(calendar, booking_id: str, start: datetime, name: str = "Marko", call_id: str | None = None):
    return calendar.add_booking(
        Booking(
            id=booking_id,
            customer_name=name,
            phone="+38970123456",
            service_key="haircut",
            service_name="Haircut",
            start=start,
            end=start + timedelta(minutes=30),
            call_id=call_id,
        )
    )


def add_call(call_service, call_id: str, started: datetime, booking_id: str | None = None, **changes) -> CallRecord:
    call = CallRecord(
        id=call_id,
        started_at=started,
        ended_at=started + timedelta(seconds=135),
        duration_seconds=135,
        ended_reason="customer-ended-call",
        transcript="AI: Hello, how can I help?\nUser: A haircut on Monday,\nplease.\nAI: You're booked!",
        summary="The caller booked a haircut.",
        cost=Decimal("0.0634"),
        customer_number="+38970123456",
        booking_id=booking_id,
    )
    return call_service.store.save_call(replace(call, **changes))


# ---------- Login ----------


@pytest.mark.parametrize("path", ["/admin", "/admin/calls", "/admin/calls/some-call"])
def test_pages_need_login(client, path):
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/admin/login?next={path}"


def test_login_page(client):
    response = client.get("/admin/login")
    assert response.status_code == 200
    assert 'name="password"' in response.text
    assert "admin_session" in response.cookies  # the session holding the CSRF token


def test_correct_login_opens_the_dashboard(client):
    response = log_in(client)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin"
    page = client.get("/admin")
    assert page.status_code == 200
    assert "Bookings this week" in page.text


def test_login_returns_to_the_page_you_wanted(client):
    assert log_in(client, next_path="/admin/calls").headers["location"] == "/admin/calls"


@pytest.mark.parametrize("next_path", ["https://evil.example", "//evil.example", "/bookings"])
def test_login_never_redirects_outside_the_admin(client, next_path):
    assert log_in(client, next_path=next_path).headers["location"] == "/admin"


@pytest.mark.parametrize(
    ("username", "password"),
    [(TEST_ADMIN_USERNAME, "wrong-password-123"), ("someone", TEST_ADMIN_PASSWORD), ("", "")],
)
def test_wrong_login_is_rejected(client, username, password):
    response = log_in(client, username=username, password=password)
    assert response.status_code == 401
    assert "Wrong username or password." in response.text
    assert client.get("/admin", follow_redirects=False).status_code == 303  # still logged out


def test_login_form_without_csrf_token_is_rejected(client):
    client.get("/admin/login")
    response = client.post(
        "/admin/login",
        data={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert client.get("/admin", follow_redirects=False).status_code == 303


def test_login_is_off_without_admin_password(admin, test_settings):
    test_settings.admin_password = None
    # Even a session from before is no longer accepted.
    assert admin.get("/admin", follow_redirects=False).status_code == 303
    page = admin.get("/admin/login").text
    assert "login is turned off" in page
    assert 'name="password"' not in page


def test_logout(admin):
    token = csrf_from(admin.get("/admin").text)
    response = admin.post("/admin/logout", data={"csrf": token}, follow_redirects=False)
    assert response.status_code == 303
    assert admin.get("/admin", follow_redirects=False).status_code == 303


def test_logout_without_csrf_token_does_nothing(admin):
    assert admin.post("/admin/logout", data={}, follow_redirects=False).status_code == 403
    assert admin.get("/admin").status_code == 200


def test_changing_the_admin_username_logs_old_sessions_out(admin, test_settings):
    test_settings.admin_username = "new-owner"
    assert admin.get("/admin", follow_redirects=False).status_code == 303


def test_admin_login_and_api_key_are_separate(client):
    # The API key (sent by the test client) does not open the dashboard...
    assert client.get("/admin", follow_redirects=False).status_code == 303
    # ...and an admin session does not open the REST API.
    log_in(client)
    assert client.get("/bookings", headers={"X-API-Key": ""}).status_code == 401


def test_pages_are_not_cached_or_framed(admin):
    response = admin.get("/admin")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"


def test_admin_pages_are_not_in_the_api_docs(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert not [p for p in paths if p.startswith("/admin")]


# ---------- Dashboard: bookings and stats ----------


def test_empty_dashboard(admin):
    page = admin.get("/admin").text
    assert "No bookings today." in page
    assert "No upcoming bookings." in page


def test_today_and_upcoming_bookings(admin, calendar):
    add_booking(calendar, "today", TODAY_15, name="Ana")
    add_booking(calendar, "monday", MONDAY_14, name="Marko", call_id="vapi-call-1")
    add_booking(calendar, "gone", MONDAY_14 + timedelta(hours=2), name="Cancelled Carl")
    calendar.cancel_booking("gone")

    page = admin.get("/admin").text
    today, upcoming = page.split("<h2>Today</h2>")[1].split("<h2>Upcoming</h2>")
    assert "Ana" in today and "15:00–15:30" in today
    assert "Marko" in upcoming and "Mon 23 Nov 14:00" in upcoming
    assert 'href="/admin/calls/vapi-call-1"' in upcoming  # booked by phone: links to the call
    assert "Cancelled Carl" not in page
    assert page.count(">Cancel</button>") == 2


def test_stats_for_this_week(admin, calendar, call_service):
    add_booking(calendar, "this-week", TODAY_15)
    add_booking(calendar, "next-week", MONDAY_14)  # Monday 23 is already next week
    week = datetime(2026, 11, 18, 10, 0, tzinfo=TIMEZONE)  # Wednesday of this week
    add_call(call_service, "c1", week, booking_id="this-week")
    add_call(call_service, "c2", week + timedelta(hours=1))
    add_call(call_service, "c3", week + timedelta(hours=2))
    add_call(call_service, "last-week", week - timedelta(days=7), booking_id="old")

    stats = admin.get("/admin").text.split('aria-label="This week"')[1].split("</section>")[0]
    values = re.findall(r'<div class="value">([^<]+)</div>', stats)
    assert values == ["1", "3", "33%"]
    assert "(1 of 3)" in stats


def test_stats_without_calls_show_a_dash(admin):
    stats = admin.get("/admin").text.split('aria-label="This week"')[1]
    assert re.findall(r'<div class="value">([^<]+)</div>', stats)[:3] == ["0", "0", "—"]


def test_cancel_button(admin, calendar):
    add_booking(calendar, "b1", MONDAY_14)
    token = csrf_from(admin.get("/admin").text)

    response = admin.post("/admin/bookings/b1/cancel", data={"csrf": token}, follow_redirects=False)
    assert response.status_code == 303
    assert calendar.get_booking("b1").status == BookingStatus.CANCELLED

    page = admin.get("/admin").text
    assert "Cancelled: Marko, Haircut on Mon 23 Nov 14:00." in page  # one-time message
    assert "No upcoming bookings." in page
    assert "Cancelled: Marko" not in admin.get("/admin").text  # shown only once


def test_cancel_unknown_booking(admin):
    token = csrf_from(admin.get("/admin").text)
    admin.post("/admin/bookings/nope/cancel", data={"csrf": token}, follow_redirects=False)
    assert "That booking doesn&#39;t exist." in admin.get("/admin").text


def test_cancel_without_csrf_token_is_rejected(admin, calendar):
    add_booking(calendar, "b1", MONDAY_14)
    assert admin.post("/admin/bookings/b1/cancel", data={"csrf": "forged"}).status_code == 403
    assert calendar.get_booking("b1").status == BookingStatus.CONFIRMED


def test_cancel_needs_login(client, calendar):
    add_booking(calendar, "b1", MONDAY_14)
    response = client.post("/admin/bookings/b1/cancel", data={"csrf": "x"}, follow_redirects=False)
    assert response.status_code == 303
    assert calendar.get_booking("b1").status == BookingStatus.CONFIRMED


def test_customer_text_is_escaped(admin, calendar):
    """Names come from callers, so they must never be able to inject HTML or JavaScript."""
    add_booking(calendar, "b1", MONDAY_14, name="<script>alert(1)</script>' onmouseover='x")
    page = admin.get("/admin").text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;&#39; onmouseover=&#39;x" in page


# ---------- Calls ----------


def test_empty_call_list(admin):
    assert "No calls yet." in admin.get("/admin/calls").text


def test_call_list(admin, call_service):
    add_call(call_service, "booked-call", MONDAY_14 - timedelta(days=3), booking_id="b1")
    add_call(
        call_service, "web-call", MONDAY_14 - timedelta(days=2), customer_number=None, ended_reason="silence-timed-out"
    )

    page = admin.get("/admin/calls").text
    assert page.index("web-call") < page.index("booked-call")  # newest first
    assert "Fri 20 Nov 14:00" in page and "2 min 15 s" in page
    assert "Customer ended call" in page and "Silence timed out" in page
    assert page.count("badge yes") == 1 and page.count("badge no") == 1
    assert "Web call" in page


def test_call_list_pages(admin, call_service):
    for n in range(30):
        add_call(call_service, f"call-{n:02}", MONDAY_14 - timedelta(hours=n))
    first = admin.get("/admin/calls").text
    assert first.count('href="/admin/calls/call-') == 25
    assert "Older →" in first and "← Newer" not in first
    second = admin.get("/admin/calls?page=2").text
    assert second.count('href="/admin/calls/call-') == 5
    assert "← Newer" in second and "Older →" not in second


def test_call_detail(admin, calendar, call_service):
    add_booking(calendar, "b1", MONDAY_14, call_id="c1")
    add_call(call_service, "c1", MONDAY_14 - timedelta(days=1), booking_id="b1")

    page = admin.get("/admin/calls/c1").text
    assert "The caller booked a haircut." in page
    assert "$0.06" in page
    assert "Haircut for Marko, Mon 23 Nov 14:00" in page and "Confirmed" in page
    # The transcript is split into speaker lines; the wrapped line is joined back together.
    lines = re.findall(r'<div class="line (\w+)"><span class="who">(\w+)</span>([^<]+)</div>', page)
    assert lines == [
        ("assistant", "Assistant", "Hello, how can I help?"),
        ("caller", "Caller", "A haircut on Monday, please."),
        ("assistant", "Assistant", "You&#39;re booked!"),
    ]


def test_call_detail_shows_a_cancelled_booking(admin, calendar, call_service):
    add_booking(calendar, "b1", MONDAY_14, call_id="c1")
    calendar.cancel_booking("b1")
    add_call(call_service, "c1", MONDAY_14 - timedelta(days=1), booking_id="b1")
    assert "badge cancelled" in admin.get("/admin/calls/c1").text


def test_call_detail_without_transcript_or_summary(admin, call_service):
    call_service.store.save_call(CallRecord(id="bare"))
    page = admin.get("/admin/calls/bare").text
    assert "No transcript." in page and "No summary" in page


def test_unknown_call_is_404(admin):
    response = admin.get("/admin/calls/does-not-exist")
    assert response.status_code == 404
    assert "That call doesn't exist." in response.text


def test_transcript_is_escaped(admin, call_service):
    add_call(call_service, "c1", MONDAY_14, transcript='User: <img src=x onerror="alert(1)">')
    page = admin.get("/admin/calls/c1").text
    assert "<img src=x" not in page
    assert "&lt;img src=x onerror=&#34;alert(1)&#34;&gt;" in page


# ---------- The display helpers ----------


def test_view_helpers():
    assert views.duration(135) == "2 min 15 s"
    assert views.duration(42) == "42 s"
    assert views.duration(None) == "—"
    assert views.ended_reason("customer-ended-call") == "Customer ended call"
    assert views.ended_reason("pipeline-error-openai-llm-failed") == "Pipeline error openai llm failed"
    assert views.money(Decimal("0.0634")) == "$0.06"
    assert views.percent(1, 3) == "33%"
    assert views.percent(0, 0) == "—"
    assert views.when(MONDAY_14) == "Mon 23 Nov 14:00"


def test_transcript_lines():
    lines = views.transcript_lines("intro text\nAI: Hi!\nbot: Still me\nUser: Hello\n  there\n\nCustomer: Bye")
    assert [(line.speaker, line.text) for line in lines] == [
        (None, "intro text"),
        ("Assistant", "Hi!"),
        ("Assistant", "Still me"),
        ("Caller", "Hello there"),
        ("Caller", "Bye"),
    ]
    assert views.transcript_lines(None) == []
