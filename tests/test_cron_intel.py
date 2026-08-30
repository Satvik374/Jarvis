"""Unit and integration tests for Cron Schedule & Timezone Intelligence Engine."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from jarvis.config import load_config
from jarvis.perception.elements import Observation
from jarvis.tools import cron_intel, registry
from jarvis.tools.schema import ACTIONS_BY_NAME


def test_schema_and_registry_registration():
    """Verify cron_intel is in schema and registered in handlers."""
    assert "cron_intel" in ACTIONS_BY_NAME
    action = ACTIONS_BY_NAME["cron_intel"]
    assert action.category == "coding"
    assert any(p.name == "op" for p in action.params)
    assert any(p.name == "expr" for p in action.params)
    assert any(p.name == "timezone_name" for p in action.params)


def test_cron_validation():
    """Test valid and invalid cron expressions."""
    res_valid = cron_intel.cron_intel(op="validate", expr="*/15 9-17 * * 1-5")
    data_v = json.loads(res_valid)
    assert data_v["valid"] is True
    assert data_v["fields"]["minute"] == "*/15"
    assert data_v["fields"]["hour"] == "9-17"

    res_invalid = cron_intel.cron_intel(op="validate", expr="65 * * * *")  # 65 > 59
    data_inv = json.loads(res_invalid)
    assert data_inv["valid"] is False
    assert "Minute error" in data_inv["error"]

    res_parts = cron_intel.cron_intel(op="validate", expr="* * *")
    data_parts = json.loads(res_parts)
    assert data_parts["valid"] is False


def test_cron_explanation():
    """Test translating cron syntax into human-readable text."""
    res = cron_intel.cron_intel(op="explain", expr="0 9 * * 1-5")
    data = json.loads(res)
    assert "At minute 0" in data["explanation"]
    assert "at 09:00" in data["explanation"]
    assert "Monday through Friday" in data["explanation"]

    res_mins = cron_intel.cron_intel(op="explain", expr="*/10 * * * *")
    data_mins = json.loads(res_mins)
    assert "Every 10 minutes" in data_mins["explanation"]


def test_upcoming_occurrences_calculation():
    """Test computing next run timestamps."""
    base_time = "2026-08-18T08:00:00+00:00"
    res = cron_intel.cron_intel(
        op="next",
        expr="0 9 * * 1-5",
        count=3,
        timezone_name="UTC",
        base_time=base_time,
    )
    data = json.loads(res)
    assert data["count"] == 3
    runs = data["next_runs"]
    assert len(runs) == 3
    # 2026-08-18 is a Tuesday (weekday). Next runs should be 2026-08-18T09:00:00, 2026-08-19T09:00:00, etc.
    assert "2026-08-18T09:00:00" in runs[0]
    assert "2026-08-19T09:00:00" in runs[1]
    assert "2026-08-20T09:00:00" in runs[2]


def test_timezone_handling():
    """Test computing occurrences in different timezones."""
    res = cron_intel.cron_intel(
        op="next",
        expr="0 12 * * *",
        count=2,
        timezone_name="Asia/Kolkata",
        base_time="2026-08-18T06:00:00+05:30",
    )
    data = json.loads(res)
    assert data["timezone"] == "Asia/Kolkata"
    assert len(data["next_runs"]) == 2
    assert "+05:30" in data["next_runs"][0] or "T12:00:00" in data["next_runs"][0]


def test_registry_execution():
    """Test executing cron_intel through registry dispatcher."""
    cfg = load_config()
    obs = Observation(
        screenshot_path=None,
        elements=[],
        screen_size=(1920, 1080),
        active_window="Cron",
    )

    res = registry.execute(
        name="cron_intel",
        args={"op": "explain", "expr": "30 4 1 * *"},
        obs=obs,
        cfg=cfg,
    )
    assert res.ok is True
    assert "At minute 30" in res.message
    assert "at 04:00" in res.message
    assert res.needs_observe is False
