"""Unit and integration tests for System Diagnostics and Self-Repair Engine."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from jarvis.config import load_config
from jarvis.perception.elements import Observation
from jarvis.tools import diagnostics, registry
from jarvis.tools.schema import ACTIONS_BY_NAME


def test_schema_and_registry_registration():
    """Verify system_diagnostics is in schema and registered in handlers."""
    assert "system_diagnostics" in ACTIONS_BY_NAME
    action = ACTIONS_BY_NAME["system_diagnostics"]
    assert action.category == "system"
    assert any(p.name == "op" for p in action.params)
    assert any(p.name == "auto_fix" for p in action.params)


def test_runtime_and_dependency_checks():
    """Test Python runtime and optional dependencies diagnostics."""
    items = diagnostics.check_runtime()
    assert len(items) >= 2
    rt_item = next(i for i in items if i.subsystem == "Runtime")
    assert "Python" in rt_item.message
    assert rt_item.status in ("OK", "WARN")


def test_brain_and_security_checks():
    """Test AI Brain and Security Credential Vault diagnostic checks."""
    cfg = load_config()
    brain_items = diagnostics.check_brain_config(cfg)
    assert any(i.subsystem == "AI Brain" for i in brain_items)

    vault_items = diagnostics.check_security_vault()
    assert any(i.subsystem == "Security Vault" for i in vault_items)


def test_memory_and_desktop_checks():
    """Test memory database integrity and desktop resolution checks."""
    mem_items = diagnostics.check_memory_db()
    assert any(i.subsystem == "Memory DB" for i in mem_items)

    desk_items = diagnostics.check_desktop()
    assert any(i.subsystem == "Desktop Display" for i in desk_items)


def test_full_health_inspection_report():
    """Test generating full health report string."""
    cfg = load_config()
    report = diagnostics.system_diagnostics(op="health", cfg=cfg)

    assert "JARVIS System Health & Diagnostics Report" in report
    assert "Runtime" in report
    assert "AI Brain" in report
    assert "Desktop Display" in report
    assert "Memory DB" in report


def test_self_repair_execution():
    """Test self-repair execution optimizing databases and verifying project paths."""
    cfg = load_config()
    repair_report = diagnostics.system_diagnostics(op="repair", cfg=cfg)

    assert "Self-Repair Completed" in repair_report


def test_registry_execution():
    """Test executing system_diagnostics through registry dispatcher."""
    cfg = load_config()
    obs = Observation(
        screenshot_path=None,
        elements=[],
        screen_size=(1920, 1080),
        active_window="System",
    )

    res = registry.execute(
        name="system_diagnostics",
        args={"op": "health", "auto_fix": False},
        obs=obs,
        cfg=cfg,
    )
    assert res.ok is True
    assert "Diagnostics Report" in res.message
    assert res.needs_observe is False
