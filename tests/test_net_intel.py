"""Unit and integration tests for Network, Port & SSL Diagnostic Engine."""

from __future__ import annotations

import json
import socket
import threading
import time

import pytest

from jarvis.config import load_config
from jarvis.perception.elements import Observation
from jarvis.tools import net_intel, registry
from jarvis.tools.schema import ACTIONS_BY_NAME


@pytest.fixture
def local_tcp_server():
    """Spin up a dummy local TCP server on an ephemeral port."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    running = True

    def _worker():
        while running:
            try:
                srv.settimeout(0.5)
                conn, _ = srv.accept()
                conn.close()
            except socket.timeout:
                continue
            except Exception:
                break

    t = threading.Thread(target=_worker, daemon=True)
    t.start()

    yield ("127.0.0.1", port)

    running = False
    srv.close()
    t.join(timeout=1.0)


def test_schema_and_registry_registration():
    """Verify net_intel is in schema and registered in handlers."""
    assert "net_intel" in ACTIONS_BY_NAME
    action = ACTIONS_BY_NAME["net_intel"]
    assert action.category == "system"
    assert any(p.name == "op" for p in action.params)
    assert any(p.name == "host" for p in action.params)
    assert any(p.name == "port" for p in action.params)


def test_port_check_open(local_tcp_server):
    """Test probing an open local TCP port."""
    host, port = local_tcp_server
    res = net_intel.net_intel(op="port_check", host=host, port=port)
    data = json.loads(res)

    assert data["status"] == "OPEN"
    assert data["host"] == host
    assert data["port"] == port
    assert data["latency_ms"] >= 0.0


def test_port_check_closed():
    """Test probing a closed port returns CLOSED or TIMEOUT."""
    res = net_intel.net_intel(op="port_check", host="127.0.0.1", port=59999, timeout=1)
    data = json.loads(res)

    assert data["status"] in ("CLOSED", "TIMEOUT", "ERROR")


def test_dns_resolution():
    """Test DNS resolution for localhost."""
    res = net_intel.net_intel(op="dns", host="localhost")
    data = json.loads(res)

    assert data["status"] == "OK"
    assert any(ip in ("127.0.0.1", "::1") for ip in data["ipv4"] + data["ipv6"])


def test_ssl_cert_inspection():
    """Test SSL inspection on public TLS host."""
    res = net_intel.net_intel(op="ssl", host="google.com", port=443)
    data = json.loads(res)

    if data.get("status") == "VALID":
        assert data["host"] == "google.com"
        assert data["tls_version"].startswith("TLS")
        assert "valid_to" in data
        assert data["days_remaining"] is not None


def test_list_listening_ports():
    """Test querying active listening ports on host machine."""
    res = net_intel.net_intel(op="listening")
    assert "Listening" in res or "PID" in res or "no listening" in res


def test_registry_execution(local_tcp_server):
    """Test executing net_intel through registry dispatcher."""
    host, port = local_tcp_server
    cfg = load_config()
    obs = Observation(
        screenshot_path=None,
        elements=[],
        screen_size=(1920, 1080),
        active_window="Network",
    )

    res = registry.execute(
        name="net_intel",
        args={"op": "port_check", "host": host, "port": port},
        obs=obs,
        cfg=cfg,
    )
    assert res.ok is True
    assert '"status": "OPEN"' in res.message
    assert res.needs_observe is False
