"""Unit and integration tests for Process Resource Monitor & Inspector Engine."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

from jarvis.config import load_config
from jarvis.perception.elements import Observation
from jarvis.tools import process_intel, registry
from jarvis.tools.schema import ACTIONS_BY_NAME


def test_schema_and_registry_registration():
    """Verify process_intel is in schema and registered in handlers."""
    assert "process_intel" in ACTIONS_BY_NAME
    action = ACTIONS_BY_NAME["process_intel"]
    assert action.category == "system"
    assert any(p.name == "op" for p in action.params)
    assert any(p.name == "pid" for p in action.params)
    assert any(p.name == "sort_by" for p in action.params)


def test_list_top_processes():
    """Test listing top memory and CPU consuming processes."""
    res = process_intel.process_intel(op="list", sort_by="memory", limit=5)
    data = json.loads(res)

    assert "processes" in data or "raw_ps" in data
    if "processes" in data:
        assert len(data["processes"]) <= 5
        assert len(data["processes"]) > 0
        p = data["processes"][0]
        assert "pid" in p
        assert "name" in p
        assert "memory_mb" in p


def test_inspect_current_process():
    """Test inspecting current Python process metadata."""
    current_pid = os.getpid()
    res = process_intel.process_intel(op="inspect", pid=current_pid)
    data = json.loads(res)

    assert data["Id"] == current_pid or data.get("pid") == current_pid
    assert "python" in data.get("Name", "").lower() or "python" in data.get("Path", "").lower() or data.get("os")


def test_find_processes():
    """Test searching processes by pattern."""
    res = process_intel.process_intel(op="find", name="python")
    data = json.loads(res)

    assert "matches" in data
    assert data["count"] >= 1
    pids = [m["pid"] for m in data["matches"]]
    assert os.getpid() in pids


def test_protected_process_refusal():
    """Test that critical OS processes cannot be terminated."""
    res_csrss = process_intel.process_intel(op="terminate", name="csrss.exe")
    assert "refused" in res_csrss.lower()

    res_kernel = process_intel.process_intel(op="terminate", pid=0)
    assert "refused" in res_kernel.lower()


def test_child_process_termination():
    """Test launching and terminating a temporary child worker process."""
    # Spawn a sleeping python child
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    child_pid = child.pid

    try:
        # Verify process is alive
        time.sleep(0.5)
        assert child.poll() is None

        # Terminate via process_intel
        res_kill = process_intel.process_intel(op="terminate", pid=child_pid, force=True)
        assert "terminated" in res_kill or "SUCCESS" in res_kill

        # Wait for child to exit
        child.wait(timeout=3.0)
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()


def test_registry_execution():
    """Test executing process_intel through registry dispatcher."""
    cfg = load_config()
    obs = Observation(
        screenshot_path=None,
        elements=[],
        screen_size=(1920, 1080),
        active_window="System",
    )

    res = registry.execute(
        name="process_intel",
        args={"op": "inspect", "pid": os.getpid()},
        obs=obs,
        cfg=cfg,
    )
    assert res.ok is True
    assert str(os.getpid()) in res.message
    assert res.needs_observe is False
