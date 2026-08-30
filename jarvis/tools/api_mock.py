"""HTTP & Webhook Mock Server Engine for Jarvis.

Enables running in-process local HTTP mock servers to intercept, simulate,
and inspect REST APIs, webhooks, and microservices during local development.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Any, Dict, List, Optional, Tuple


class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _MockState:
    def __init__(self, port: int, server: _ThreadingHTTPServer, thread: threading.Thread):
        self.port = port
        self.server = server
        self.thread = thread
        self.routes: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.requests: List[Dict[str, Any]] = []
        self.lock = threading.Lock()


class _MockManager:
    _instance: Optional["_MockManager"] = None

    def __init__(self):
        self._servers: Dict[int, _MockState] = {}
        self._lock = threading.Lock()

    @classmethod
    def get(cls) -> "_MockManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def start_server(self, port: int = 8999) -> Dict[str, Any]:
        with self._lock:
            if port in self._servers:
                state = self._servers[port]
                return {
                    "status": "already_running",
                    "port": port,
                    "url": f"http://127.0.0.1:{port}",
                    "routes_count": len(state.routes),
                }

            manager = self

            class _Handler(BaseHTTPRequestHandler):
                def log_message(self, format, *args):
                    pass  # Suppress console logging

                def _handle_any(self, method: str):
                    state = manager._servers.get(port)
                    if not state:
                        self.send_response(503)
                        self.end_headers()
                        return

                    # Parse body
                    content_len = int(self.headers.get("Content-Length", 0))
                    body_bytes = self.rfile.read(content_len) if content_len > 0 else b""
                    body_text = body_bytes.decode("utf-8", errors="replace")

                    req_record = {
                        "method": method,
                        "path": self.path,
                        "headers": dict(self.headers),
                        "body": body_text,
                        "client_ip": self.client_address[0],
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }

                    with state.lock:
                        state.requests.append(req_record)
                        # Find route
                        clean_path = self.path.split("?")[0]
                        route = state.routes.get((method.upper(), clean_path)) or state.routes.get(("*", clean_path)) or state.routes.get(("*", "*"))

                    if route:
                        delay = float(route.get("delay", 0.0))
                        if delay > 0:
                            time.sleep(delay)

                        status = int(route.get("status", 200))
                        self.send_response(status)

                        # Response headers
                        custom_headers = route.get("headers", {})
                        if isinstance(custom_headers, dict):
                            for k, v in custom_headers.items():
                                self.send_header(str(k), str(v))

                        if "Content-Type" not in custom_headers and "content-type" not in custom_headers:
                            self.send_header("Content-Type", "application/json")

                        resp_body = route.get("body", "")
                        if not isinstance(resp_body, (str, bytes)):
                            resp_body = json.dumps(resp_body)
                        if isinstance(resp_body, str):
                            resp_bytes = resp_body.encode("utf-8")
                        else:
                            resp_bytes = resp_body

                        self.send_header("Content-Length", str(len(resp_bytes)))
                        self.end_headers()
                        self.wfile.write(resp_bytes)
                    else:
                        # Default 200 JSON echo
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        resp_data = json.dumps({
                            "message": "Mock default response",
                            "received_method": method,
                            "received_path": self.path,
                        }).encode("utf-8")
                        self.send_header("Content-Length", str(len(resp_data)))
                        self.end_headers()
                        self.wfile.write(resp_data)

                def do_GET(self):
                    self._handle_any("GET")

                def do_POST(self):
                    self._handle_any("POST")

                def do_PUT(self):
                    self._handle_any("PUT")

                def do_DELETE(self):
                    self._handle_any("DELETE")

                def do_PATCH(self):
                    self._handle_any("PATCH")

                def do_OPTIONS(self):
                    self.send_response(204)
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, PATCH, OPTIONS")
                    self.send_header("Access-Control-Allow-Headers", "*")
                    self.end_headers()

            server = _ThreadingHTTPServer(("127.0.0.1", port), _Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            state = _MockState(port=port, server=server, thread=thread)
            self._servers[port] = state

            return {
                "status": "started",
                "port": port,
                "url": f"http://127.0.0.1:{port}",
            }

    def add_route(
        self,
        port: int,
        path: str,
        method: str = "GET",
        status: int = 200,
        body: Any = "",
        headers: Optional[Dict[str, str]] = None,
        delay: float = 0.0,
    ) -> Dict[str, Any]:
        with self._lock:
            state = self._servers.get(port)
            if not state:
                # Auto-start server if not running
                self.start_server(port)
                state = self._servers[port]

            clean_method = (method or "GET").strip().upper()
            clean_path = (path or "/").strip()
            if not clean_path.startswith("/") and clean_path != "*":
                clean_path = "/" + clean_path

            with state.lock:
                state.routes[(clean_method, clean_path)] = {
                    "status": int(status or 200),
                    "body": body,
                    "headers": headers or {"Content-Type": "application/json"},
                    "delay": float(delay or 0.0),
                }

            return {
                "status": "route_added",
                "port": port,
                "method": clean_method,
                "path": clean_path,
                "response_status": int(status or 200),
            }

    def get_history(self, port: int) -> Dict[str, Any]:
        with self._lock:
            state = self._servers.get(port)
            if not state:
                return {"port": port, "status": "server_not_running", "count": 0, "requests": []}

            with state.lock:
                reqs = list(state.requests)

            return {
                "port": port,
                "status": "running",
                "total_recorded": len(reqs),
                "requests": reqs,
            }

    def clear(self, port: int) -> Dict[str, Any]:
        with self._lock:
            state = self._servers.get(port)
            if state:
                with state.lock:
                    state.requests.clear()
                    state.routes.clear()
            return {"port": port, "status": "cleared"}

    def stop_server(self, port: Optional[int] = None) -> Dict[str, Any]:
        with self._lock:
            if port is not None:
                state = self._servers.pop(port, None)
                if state:
                    state.server.shutdown()
                    state.server.server_close()
                    return {"port": port, "status": "stopped"}
                return {"port": port, "status": "not_running"}
            else:
                ports = list(self._servers.keys())
                for p in ports:
                    state = self._servers.pop(p)
                    state.server.shutdown()
                    state.server.server_close()
                return {"stopped_ports": ports, "status": "all_stopped"}


# --------------------------------------------------------------------------- #
# Main Entrypoint
# --------------------------------------------------------------------------- #

def api_mock(
    op: str = "start",
    port: int = 8999,
    path: str = "/",
    method: str = "GET",
    status: int = 200,
    body: Any = "",
    headers: Optional[Dict[str, str]] = None,
    delay: float = 0.0,
    allow: tuple[str, ...] = (),
) -> str:
    """HTTP & Webhook Mock Server Engine.

    Operations:
      - 'start': Launch mock server on specified port (default 8999).
      - 'route' / 'mock': Register endpoint route and response payload.
      - 'history' / 'requests': Inspect recorded incoming requests.
      - 'clear': Clear captured request log and routes.
      - 'stop': Shut down mock server.
    """
    op_clean = (op or "start").strip().lower()
    mgr = _MockManager.get()
    p_num = int(port or 8999)

    if op_clean in ("start", "launch", "init"):
        res = mgr.start_server(port=p_num)
        return json.dumps(res, indent=2)

    elif op_clean in ("route", "mock", "add"):
        res = mgr.add_route(
            port=p_num,
            path=path,
            method=method,
            status=status,
            body=body,
            headers=headers,
            delay=delay,
        )
        return json.dumps(res, indent=2)

    elif op_clean in ("history", "requests", "inspect", "log"):
        res = mgr.get_history(port=p_num)
        return json.dumps(res, indent=2)

    elif op_clean in ("clear", "reset"):
        res = mgr.clear(port=p_num)
        return json.dumps(res, indent=2)

    elif op_clean in ("stop", "shutdown", "close"):
        res = mgr.stop_server(port=p_num if port else None)
        return json.dumps(res, indent=2)

    return f"unknown api_mock op '{op}' - supported: start, route, history, clear, stop"
