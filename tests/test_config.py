"""Tests for settings loaded from environment variables."""

from datetime import time

import pytest
from pydantic import ValidationError

from app.config import Settings


def make_settings(**overrides) -> Settings:
    # _env_file=None: ignore any real .env file on this computer.
    return Settings(_env_file=None, **overrides)


def test_defaults_match_the_shop():
    s = make_settings()
    assert s.shop_timezone == "Europe/Skopje"
    assert s.opening_hours[0] == (time(9, 0), time(18, 0))  # Monday
    assert 6 not in s.opening_hours  # Sunday closed
    assert [svc.duration_minutes for svc in s.services] == [30, 20, 50]


def test_environment_variables_override_defaults(monkeypatch):
    monkeypatch.setenv("CLOSE_TIME", "19:30")
    monkeypatch.setenv("OPEN_DAYS", "tue,wed")
    s = make_settings()
    assert s.close_time == time(19, 30)
    assert sorted(s.opening_hours) == [1, 2]


def test_services_can_be_set_as_json(monkeypatch):
    monkeypatch.setenv("SERVICES", '[{"key": "shave", "name": "Shave", "duration_minutes": 15}]')
    assert make_settings().services[0].name == "Shave"


def test_empty_values_in_env_file_mean_not_set(tmp_path):
    """Copying .env.example leaves e.g. `VAPI_SECRET=` empty; that must not stop the server from starting."""
    env_file = tmp_path / ".env"
    env_file.write_text("VAPI_SECRET=\nADMIN_PASSWORD=\nSESSION_SECRET=\nADMIN_USERNAME=\n", encoding="utf-8")
    s = Settings(_env_file=env_file)
    assert s.vapi_secret is None
    assert s.admin_password is None
    assert s.session_secret is None
    assert s.admin_username == "admin"  # the default


@pytest.mark.parametrize(
    "overrides",
    [
        {"close_time": "08:00"},  # closes before it opens
        {"open_days": "mon,funday"},  # not a day
        {"shop_timezone": "Mars/Olympus"},  # not a timezone
        {"slot_step_minutes": 0},
        {"admin_password": "short"},  # at least 10 characters
        {"session_secret": "too-short"},  # at least 32 characters
    ],
)
def test_invalid_settings_are_rejected(overrides):
    with pytest.raises(ValidationError):
        make_settings(**overrides)
