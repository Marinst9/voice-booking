"""Tests for the X-API-Key check."""

import asyncio

import pytest

from app.config import Settings
from app.main import app, lifespan

ENDPOINTS = [
    ("post", "/check_availability", {"date": "2026-11-23", "service": "Haircut"}),
    (
        "post",
        "/create_booking",
        {"customer_name": "Marko", "phone": "070123456", "service": "Haircut", "start_time": "2026-11-23T14:00"},
    ),
    ("post", "/cancel_booking", {"phone": "070123456", "start_time": "2026-11-23T14:00"}),
    ("get", "/bookings", None),
]


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
def test_every_endpoint_rejects_missing_key(client, method, path, body):
    response = client.request(method, path, json=body, headers={"X-API-Key": ""})
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_api_key"


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
def test_every_endpoint_rejects_wrong_key(client, method, path, body):
    response = client.request(method, path, json=body, headers={"X-API-Key": "wrong-key-wrong-key"})
    assert response.status_code == 401


def test_correct_key_is_accepted(client):
    # The `client` fixture sends the right key by default.
    assert client.get("/bookings").status_code == 200


def test_auth_is_checked_before_input(client):
    # A bad body with no key must say "unauthorized", not leak validation details.
    response = client.post("/create_booking", json={}, headers={"X-API-Key": ""})
    assert response.status_code == 401


def test_docs_stay_open_without_key(client):
    for path in ["/docs", "/openapi.json"]:
        assert client.get(path, headers={"X-API-Key": ""}).status_code == 200


def test_docs_have_authorize_button(client):
    spec = client.get("/openapi.json").json()
    scheme = spec["components"]["securitySchemes"]["APIKeyHeader"]
    assert scheme == {**scheme, "type": "apiKey", "in": "header", "name": "X-API-Key"}
    assert spec["paths"]["/create_booking"]["post"]["security"] == [{"APIKeyHeader": []}]


def test_no_key_configured_rejects_everything(client, test_settings):
    test_settings.api_key = None
    assert client.get("/bookings").status_code == 401


def test_server_refuses_to_start_without_key(monkeypatch):
    monkeypatch.setattr("app.main.get_settings", lambda: Settings(_env_file=None, api_key=None))

    async def start_server():
        async with lifespan(app):
            pass

    with pytest.raises(RuntimeError, match="API_KEY is not set"):
        asyncio.run(start_server())


def test_short_key_is_rejected_by_settings():
    with pytest.raises(ValueError):
        Settings(_env_file=None, api_key="short")
