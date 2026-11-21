"""
Tests for the Vapi adapter (POST /vapi/tools), using payloads shaped like the ones Vapi sends.

Like the API tests, these run against both the in-memory calendar and PostgreSQL,
with the fake "now" of Sunday 22 November 2026, 12:00 (Skopje).
"""

import json
from pathlib import Path

import pytest

from app.config import SERVICES
from app.integrations.vapi import TOOLS
from tests.conftest import TEST_API_KEY, TEST_VAPI_SECRET

VAPI_HEADERS = {"Authorization": f"Bearer {TEST_VAPI_SECRET}", "X-API-Key": ""}
TOOLS_DIR = Path(__file__).resolve().parents[1] / "vapi" / "tools"


def tool_call(name: str, arguments: dict | str | None = None, call_id: str = "call_1") -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments or {}}}


def vapi_payload(*calls: dict) -> dict:
    """A tool-calls webhook body like Vapi sends it (trimmed: real ones carry much more call data)."""
    return {
        "message": {
            "type": "tool-calls",
            "timestamp": 1795339200000,
            "toolCallList": list(calls),
            "toolWithToolCallList": [
                {"type": "function", "function": {"name": c["function"]["name"]}, "toolCall": c} for c in calls
            ],
            "call": {"id": "vapi-call-42", "type": "webCall", "orgId": "org-1"},
            "assistant": {"name": "Barber receptionist"},
        }
    }


def call_tools(client, *calls: dict, headers: dict | None = None):
    return client.post("/vapi/tools", json=vapi_payload(*calls), headers=headers or VAPI_HEADERS)


def only_result(response) -> dict:
    """The single result entry, with its `result` string decoded from JSON."""
    assert response.status_code == 200
    [entry] = response.json()["results"]
    assert "error" not in entry
    assert "\n" not in entry["result"]  # Vapi requires a single-line string
    return {**entry, "result": json.loads(entry["result"])}


BOOKING_ARGS = {
    "customer_name": "Marko",
    "phone": "+389 70 123 456",
    "service": "Haircut",
    "start_time": "2026-11-23T14:00",
}


# ---------- The four tools ----------


def test_check_availability(client):
    entry = only_result(
        call_tools(client, tool_call("check_availability", {"date": "2026-11-23", "service": "Haircut"}, "call_abc"))
    )
    assert entry["toolCallId"] == "call_abc"
    assert entry["name"] == "check_availability"
    assert entry["result"]["success"] is True
    assert entry["result"]["free_times"][:2] == ["09:00", "09:30"]
    assert "Monday 23 November" in entry["result"]["message"]


def test_create_booking(client):
    result = only_result(call_tools(client, tool_call("create_booking", BOOKING_ARGS)))["result"]
    assert result["success"] is True
    assert result["message"] == "You're booked, Marko! Haircut on Monday 23 November at 14:00."
    assert result["booking"]["status"] == "confirmed"
    # It really went into the calendar the REST API uses.
    assert client.get("/bookings").json()["count"] == 1


def test_taken_slot_is_a_normal_result_with_suggestions(client):
    call_tools(client, tool_call("create_booking", BOOKING_ARGS))
    result = only_result(call_tools(client, tool_call("create_booking", {**BOOKING_ARGS, "phone": "070999888"})))[
        "result"
    ]
    # In `result` (not `error`), so the assistant reads the message and offers the alternatives.
    assert result["success"] is False
    assert result["error"] == "time_taken"
    assert result["suggested_times"] == ["13:30", "14:30"]
    assert "already taken" in result["message"]


def test_closed_day_is_explained(client):
    result = only_result(
        call_tools(client, tool_call("check_availability", {"date": "2026-11-29", "service": "Haircut"}))
    )["result"]
    assert result["error"] == "closed_day"
    assert "closed on Sundays" in result["message"]


def test_cancel_booking(client):
    call_tools(client, tool_call("create_booking", BOOKING_ARGS))
    # Same number, written differently (as speech-to-text might produce it).
    cancel_args = {"phone": "+389 70-123-456", "start_time": "2026-11-23T14:00"}
    result = only_result(call_tools(client, tool_call("cancel_booking", cancel_args)))["result"]
    assert result["success"] is True
    assert result["booking"]["status"] == "cancelled"


def test_cancel_unknown_booking(client):
    result = only_result(
        call_tools(client, tool_call("cancel_booking", {"phone": "070111222", "start_time": "2026-11-23T14:00"}))
    )["result"]
    assert result["error"] == "booking_not_found"


def test_get_current_datetime(client):
    result = only_result(call_tools(client, tool_call("get_current_datetime")))["result"]
    assert result["now"] == "2026-11-22T12:00+01:00"
    assert result["weekday"] == "Sunday"
    assert result["timezone"] == "Europe/Skopje"
    assert result["open_now"] is False
    days = result["upcoming_days"]
    assert len(days) == 14
    assert days[0] == {"date": "2026-11-22", "weekday": "Sunday", "open": False}  # opening_hours None is left out
    assert days[1] == {"date": "2026-11-23", "weekday": "Monday", "open": True, "opening_hours": "09:00-18:00"}
    # "Next Monday" can be looked up instead of calculated:
    assert [d["date"] for d in days if d["weekday"] == "Monday"] == ["2026-11-23", "2026-11-30"]


# ---------- Payload variations ----------


def test_arguments_sent_as_json_string(client):
    args = json.dumps({"date": "2026-11-23", "service": "Beard trim"})
    result = only_result(call_tools(client, tool_call("check_availability", args)))["result"]
    assert result["service"] == "Beard trim"


def test_several_tool_calls_in_one_request(client):
    response = call_tools(
        client,
        tool_call("get_current_datetime", call_id="first"),
        tool_call("check_availability", {"date": "2026-11-23", "service": "Haircut"}, "second"),
    )
    results = response.json()["results"]
    assert [r["toolCallId"] for r in results] == ["first", "second"]


def test_missing_argument_tells_the_assistant_what_to_fix(client):
    result = only_result(call_tools(client, tool_call("create_booking", {"customer_name": "Marko"})))["result"]
    assert result["error"] == "invalid_input"
    assert "phone" in result["message"] and "start_time" in result["message"]


def test_unreadable_arguments(client):
    result = only_result(call_tools(client, tool_call("check_availability", "{not json")))["result"]
    assert result["error"] == "invalid_input"


def test_unknown_tool_uses_error_field(client):
    response = call_tools(client, tool_call("order_pizza"))
    assert response.status_code == 200
    [entry] = response.json()["results"]
    assert "result" not in entry
    assert "Unknown tool 'order_pizza'" in entry["error"]


def test_server_crash_uses_error_field_and_still_returns_200(client, booking_service, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("database exploded")

    monkeypatch.setattr(booking_service, "check_availability", broken)
    response = call_tools(client, tool_call("check_availability", {"date": "2026-11-23", "service": "Haircut"}))
    assert response.status_code == 200
    [entry] = response.json()["results"]
    assert entry["error"] == "The booking system had an internal error."
    assert "exploded" not in response.text


def test_other_vapi_events_are_acknowledged(client):
    payload = {"message": {"type": "status-update", "status": "in-progress", "call": {"id": "vapi-call-42"}}}
    response = client.post("/vapi/tools", json=payload, headers=VAPI_HEADERS)
    assert response.status_code == 200
    assert response.json() == {"results": []}


# ---------- Security ----------


def test_legacy_x_vapi_secret_header_works(client):
    response = call_tools(client, tool_call("get_current_datetime"), headers={"X-Vapi-Secret": TEST_VAPI_SECRET})
    assert response.status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {},  # nothing
        {"Authorization": "Bearer wrong-secret-wrong-secret"},
        {"X-Vapi-Secret": "wrong-secret-wrong-secret"},
        {"Authorization": f"Bearer {TEST_API_KEY}"},  # the REST API key is not the Vapi secret
        {"X-API-Key": TEST_API_KEY},
    ],
)
def test_wrong_or_missing_vapi_secret_is_rejected(client, headers):
    response = client.post(
        "/vapi/tools", json=vapi_payload(tool_call("get_current_datetime")), headers={"X-API-Key": "", **headers}
    )
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_vapi_secret"


def test_vapi_secret_does_not_open_the_rest_api(client):
    response = client.get("/bookings", headers={"X-API-Key": "", "X-Vapi-Secret": TEST_VAPI_SECRET})
    assert response.status_code == 401


def test_no_vapi_secret_configured_rejects_everything(client, test_settings):
    test_settings.vapi_secret = None
    assert call_tools(client, tool_call("get_current_datetime")).status_code == 401


# ---------- The tool definitions in vapi/tools/ ----------


def load_tool_files() -> dict[str, dict]:
    return {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in TOOLS_DIR.glob("*.json")}


def test_every_tool_has_a_definition_file_and_vice_versa():
    files = load_tool_files()
    assert set(files) == set(TOOLS)
    for stem, tool in files.items():
        assert tool["type"] == "function"
        assert tool["function"]["name"] == stem
        assert tool["server"]["url"].endswith("/vapi/tools")


def test_tool_parameters_match_the_request_models(client):
    """The parameters Vapi is told about must be exactly what the API accepts."""
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    models = {
        "check_availability": "CheckAvailabilityRequest",
        "create_booking": "CreateBookingRequest",
        "cancel_booking": "CancelBookingRequest",
    }
    for name, tool in load_tool_files().items():
        parameters = tool["function"]["parameters"]
        if name in models:
            model = schemas[models[name]]
            assert set(parameters["properties"]) == set(model["properties"]), name
            assert set(parameters["required"]) == set(model["required"]), name
        if "service" in parameters["properties"]:
            assert parameters["properties"]["service"]["enum"] == [s.name for s in SERVICES]
