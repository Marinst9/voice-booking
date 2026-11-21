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


@pytest.mark.parametrize(
    "overrides",
    [
        {"close_time": "08:00"},  # closes before it opens
        {"open_days": "mon,funday"},  # not a day
        {"shop_timezone": "Mars/Olympus"},  # not a timezone
        {"slot_step_minutes": 0},
    ],
)
def test_invalid_settings_are_rejected(overrides):
    with pytest.raises(ValidationError):
        make_settings(**overrides)
