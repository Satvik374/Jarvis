"""Unit and integration tests for HTTP & Webhook Mock Server Engine."""

from __future__ import annotations

import json
import urllib.request
import urllib.parse

import pytest

from jarvis.config import load_config
from jarvis.perception.elements import Observation
from jarvis.tools import api_mock, registry
from jarvis.tools.schema import ACTIONS_BY_NAME


@pytest.fixture(autouse=True)
def cleanup_servers():
    yield
    api_mock.api_mock(op="stop")


def test_schema_and_registry_registration():
    """Verify api_mock is in schema and registered in handlers."""
    assert "api_mock" in ACTIONS_BY_NAME
    action = ACTIONS_BY_NAME["api_mock"]
    assert action.category == "coding"
    assert any(p.name == "op" for p in action.params)
    assert any(p.name == "port" for p in action.params)
    assert any(p.name == "path" for p in action.params)


def test_mock_server_lifecycle_and_routes():
    """Test starting mock server, registering GET/POST routes, and inspecting history."""
    # 1. Start server on an ephemeral port: the OS assigns it and we read it back
    #    from the live socket, so the test owns the port instead of depending on a
    #    fixed one (9188) happening to be free on this machine.
    res_start = api_mock.api_mock(op="start", port=0)
    data_start = json.loads(res_start)
    assert data_start["status"] in ("started", "already_running")
    test_port = data_start["port"]
    assert test_port > 0
    assert data_start["url"] == f"http://127.0.0.1:{test_port}"

    # 2. Add GET route
    api_mock.api_mock(
        op="route",
        port=test_port,
        path="/api/status",
        method="GET",
        status=200,
        body={"status": "online", "version": "1.0"},
    )

    # 3. Call GET endpoint
    req = urllib.request.Request(f"http://127.0.0.1:{test_port}/api/status")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["status"] == "online"

    # 4. Add POST route
    api_mock.api_mock(
        op="route",
        port=test_port,
        path="/webhook/event",
        method="POST",
        status=201,
        body={"received": True},
    )

    # 5. Call POST endpoint
    post_data = json.dumps({"event": "payment_success", "amount": 99.0}).encode("utf-8")
    req_post = urllib.request.Request(
        f"http://127.0.0.1:{test_port}/webhook/event",
        data=post_data,
        headers={"Content-Type": "application/json", "X-Custom-Auth": "Secret123"},
        method="POST",
    )
    with urllib.request.urlopen(req_post) as resp_post:
        assert resp_post.status == 201
        data_post = json.loads(resp_post.read().decode("utf-8"))
        assert data_post["received"] is True

    # 6. Check recorded history
    res_hist = api_mock.api_mock(op="history", port=test_port)
    data_hist = json.loads(res_hist)
    assert data_hist["total_recorded"] >= 2

    # Find the POST request in history
    post_entry = next((r for r in data_hist["requests"] if r["method"] == "POST"), None)
    assert post_entry is not None
    assert "/webhook/event" in post_entry["path"]
    assert "payment_success" in post_entry["body"]
    assert post_entry["headers"].get("X-Custom-Auth") == "Secret123"

    # 7. Clear history
    api_mock.api_mock(op="clear", port=test_port)
    hist_cleared = json.loads(api_mock.api_mock(op="history", port=test_port))
    assert hist_cleared["total_recorded"] == 0

    # 8. Stop server
    res_stop = api_mock.api_mock(op="stop", port=test_port)
    assert json.loads(res_stop)["status"] == "stopped"


def test_registry_execution():
    """Test executing api_mock through registry dispatcher."""
    cfg = load_config()
    obs = Observation(
        screenshot_path=None,
        elements=[],
        screen_size=(1920, 1080),
        active_window="Mock",
    )

    # Port 0: the OS assigns a free port, so no fixed port (9199) can deny the bind.
    res = registry.execute(
        name="api_mock",
        args={"op": "start", "port": 0},
        obs=obs,
        cfg=cfg,
    )
    assert res.ok is True
    started = json.loads(res.message)
    assert started["status"] == "started"
    test_port = started["port"]
    assert test_port > 0
    assert f'"port": {test_port}' in res.message
    assert res.needs_observe is False

    # Stop server via registry
    res_stop = registry.execute(
        name="api_mock",
        args={"op": "stop", "port": test_port},
        obs=obs,
        cfg=cfg,
    )
    assert res_stop.ok is True
