"""Unit and integration tests for Interactive Stateful Shell Session Engine."""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

from jarvis.config import load_config
from jarvis.perception.elements import Observation
from jarvis.tools import registry, session_exec
from jarvis.tools.schema import ACTIONS_BY_NAME
from jarvis.tools.session_exec import get_session_manager


@pytest.fixture(autouse=True)
def clean_sessions():
    mgr = get_session_manager()
    yield
    mgr.close()  # Close all open sessions after test


def test_schema_and_registry_registration():
    """Verify session_exec is in schema and registered in handlers."""
    assert "session_exec" in ACTIONS_BY_NAME
    action = ACTIONS_BY_NAME["session_exec"]
    assert action.category == "system"
    assert any(p.name == "command" for p in action.params)
    assert any(p.name == "op" for p in action.params)
    assert any(p.name == "name" for p in action.params)
    assert any(p.name == "shell_type" for p in action.params)


def test_session_lifecycle():
    """Test start, list, and close of sessions."""
    mgr = get_session_manager()

    res_start = mgr.start(name="test_sess", shell_type="powershell")
    assert "started" in res_start or "running" in res_start

    res_list = mgr.list_sessions()
    assert "test_sess" in res_list
    assert "powershell" in res_list

    res_close = mgr.close(name="test_sess")
    assert "closed" in res_close

    res_list_empty = mgr.list_sessions()
    assert "test_sess" not in res_list_empty


def test_powershell_state_persistence():
    """Test state persistence across turns in PowerShell session."""
    mgr = get_session_manager()

    # Turn 1: Set variable
    mgr.execute("$TEST_STATE_VAR = 987654", name="ps_state")

    # Turn 2: Read variable back
    out2 = mgr.execute("Write-Output $TEST_STATE_VAR", name="ps_state")
    assert "987654" in out2

    # Turn 3: Perform arithmetic on variable
    out3 = mgr.execute("Write-Output ($TEST_STATE_VAR + 1)", name="ps_state")
    assert "987655" in out3


def test_python_repl_state_persistence():
    """Test state persistence across turns in interactive Python session."""
    mgr = get_session_manager()

    mgr.start(name="py_state", shell_type="python")

    # Step 1: Assign data structure
    mgr.execute("data_map = {'user': 'Jarvis', 'score': 100}", name="py_state")

    # Step 2: Query map and print result
    out = mgr.execute("print(f\"{data_map['user']} has score {data_map['score'] * 2}\")", name="py_state")
    assert "Jarvis has score 200" in out


def test_registry_execution():
    """Test running session_exec via the registry dispatcher."""
    cfg = load_config()
    obs = Observation(
        screenshot_path=None,
        elements=[],
        screen_size=(1920, 1080),
        active_window="Terminal",
    )

    # 1. Execute command in default session
    res1 = registry.execute(
        name="session_exec",
        args={"op": "exec", "command": "Write-Output 'Jarvis Active'", "name": "reg_test"},
        obs=obs,
        cfg=cfg,
    )
    assert res1.ok is True
    assert "Jarvis Active" in res1.message
    assert res1.needs_observe is False

    # 2. List sessions
    res2 = registry.execute(
        name="session_exec",
        args={"op": "list"},
        obs=obs,
        cfg=cfg,
    )
    assert res2.ok is True
    assert "reg_test" in res2.message
