"""Local browser frontend for Jarvis.

The page is intentionally a thin interface over the existing terminal REPL:
the REPL runs in a child Python process, stdin carries directives, stdout
remains the terminal transcript, and structured stderr events drive the UI.
No web framework or internet connection is required.
"""

from __future__ import annotations

import base64
import codecs
from collections import deque
import hmac
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import queue
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any
from urllib.parse import parse_qs, quote, urlparse
import webbrowser

from .browser_worker import EVENT_PREFIX
from .config import ROOT


HOST = "127.0.0.1"
STATIC_DIR = Path(__file__).resolve().parent / "browser_ui"
INPUT_PREFIX = "__JARVIS_BROWSER_INPUT64__:"
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_MAX_INPUT_BYTES = 256 * 1024
_MAX_INPUT_BODY = _MAX_INPUT_BYTES * 2 + 16 * 1024
_MAX_ATTACHMENT_BODY = 20 * 1024 * 1024
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


class EventBroker:
    """Thread-safe bounded replay buffer plus one queue per SSE client."""

    def __init__(self, history_size: int = 300, client_queue_size: int = 400):
        self._history: deque[dict[str, Any]] = deque(maxlen=history_size)
        self._subscribers: set[queue.Queue] = set()
        self._client_queue_size = client_queue_size
        self._next_id = 1
        self._lock = threading.Lock()

    def publish(self, event: str, **payload: Any) -> dict[str, Any]:
        with self._lock:
            record = {
                "id": self._next_id,
                "event": event,
                "timestamp": time.time(),
                **payload,
            }
            self._next_id += 1
            self._history.append(record)
            for subscriber in tuple(self._subscribers):
                try:
                    subscriber.put_nowait(record)
                except queue.Full:
                    # A hidden/stalled tab must not slow the Jarvis backend.
                    try:
                        subscriber.get_nowait()
                        subscriber.put_nowait(record)
                    except (queue.Empty, queue.Full):
                        pass
            return record

    def subscribe(self) -> tuple[queue.Queue, list[dict[str, Any]]]:
        subscriber: queue.Queue = queue.Queue(maxsize=self._client_queue_size)
        with self._lock:
            self._subscribers.add(subscriber)
            history = list(self._history)
        return subscriber, history

    def unsubscribe(self, subscriber: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)


class TerminalBridge:
    """Own the terminal child and translate its three streams into UI events."""

    def __init__(
        self,
        child_args: list[str] | None = None,
        initial_task: str | None = None,
        token: str | None = None,
    ):
        self.child_args = list(child_args or [])
        self.initial_task = initial_task
        self.token = token or secrets.token_urlsafe(32)
        self.launch_cwd = Path.cwd()
        self.broker = EventBroker()
        self.process: subprocess.Popen[bytes] | None = None
        self.stopped = threading.Event()
        self.accepting_input = False
        self.input_mode = "command"
        self.input_prompt = ""
        self.state = "booting"
        self.speech_active = False
        self.speech_utterance_id = 0
        self.speech_duration_ms = 0
        self.speech_levels: list[int] = []
        self.speech_bands = ""
        self.speech_band_count = 0
        self.speech_band_fps = 0
        self.speech_started_at = 0.0
        self._initial_sent = False
        self._shutdown_pending = False
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._process_exited = threading.Event()
        self._attachments: list[Path] = []

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self) -> None:
        if self.process is not None:
            raise RuntimeError("terminal bridge already started")

        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        env.setdefault("PYTHONUTF8", "1")
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            str(ROOT)
            if not existing_pythonpath
            else str(ROOT) + os.pathsep + existing_pythonpath
        )
        command = [
            sys.executable,
            "-u",
            str(ROOT / "jarvis" / "browser_worker.py"),
            *self.child_args,
        ]
        popen_options: dict[str, Any] = {
            "cwd": str(self.launch_cwd),
            "env": env,
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "bufsize": 0,
        }
        if os.name == "nt":
            popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        self.process = subprocess.Popen(command, **popen_options)
        self.broker.publish(
            "session",
            alive=True,
            pid=self.process.pid,
            message="Local terminal runtime started",
        )
        threading.Thread(
            target=self._read_stdout,
            name="jarvis-browser-stdout",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._read_stderr,
            name="jarvis-browser-events",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._watch_process,
            name="jarvis-browser-watch",
            daemon=True,
        ).start()

    def _read_stdout(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            while True:
                chunk = self.process.stdout.read(2048)
                if not chunk:
                    break
                text = decoder.decode(chunk)
                clean = _ANSI_RE.sub("", text).replace("\r", "")
                if clean:
                    self.broker.publish("terminal", text=clean)
            tail = decoder.decode(b"", final=True)
            if tail:
                self.broker.publish(
                    "terminal",
                    text=_ANSI_RE.sub("", tail).replace("\r", ""),
                )
        except (OSError, ValueError) as exc:
            if self.alive:
                self.broker.publish(
                    "activity",
                    kind="warning",
                    message=f"Terminal stream interrupted: {exc}",
                )

    def _read_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        try:
            while True:
                raw = self.process.stderr.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if line.startswith(EVENT_PREFIX):
                    try:
                        payload = json.loads(line[len(EVENT_PREFIX):])
                    except json.JSONDecodeError:
                        continue
                    self._handle_structured(payload)
                elif line:
                    self.broker.publish("terminal", text=line + "\n")
        except (OSError, ValueError) as exc:
            if self.alive:
                self.broker.publish(
                    "activity",
                    kind="warning",
                    message=f"Event stream interrupted: {exc}",
                )

    def _handle_structured(self, payload: dict[str, Any]) -> None:
        event = str(payload.pop("event", "activity"))
        if event == "state":
            with self._state_lock:
                self.state = str(payload.get("state", self.state))
        elif event == "input_request":
            with self._state_lock:
                self.accepting_input = True
                self.input_mode = str(payload.get("mode", "command"))
                self.input_prompt = str(payload.get("prompt", ""))
        elif event == "speech":
            try:
                utterance_id = max(0, int(payload.get("utterance_id", 0)))
            except (TypeError, ValueError):
                utterance_id = 0
            active = bool(payload.get("active"))
            try:
                duration_ms = max(
                    0,
                    int(payload.get("duration_ms", 0) or 0),
                )
            except (TypeError, ValueError, OverflowError):
                duration_ms = 0
            levels: list[int] = []
            raw_levels = payload.get("levels", [])
            if isinstance(raw_levels, list):
                for value in raw_levels[:96]:
                    try:
                        levels.append(max(0, min(255, int(value))))
                    except (TypeError, ValueError, OverflowError):
                        continue
            try:
                band_count = max(0, min(64, int(payload.get("band_count", 0) or 0)))
                band_fps = max(0, min(120, int(payload.get("band_fps", 0) or 0)))
            except (TypeError, ValueError, OverflowError):
                band_count = band_fps = 0
            raw_bands = payload.get("bands", "")
            # base64 uint8 matrix; cap it so a malformed record cannot pin
            # unbounded memory in the bridge or the SSE snapshot.
            bands = raw_bands if isinstance(raw_bands, str) else ""
            if len(bands) > 262144:
                bands, band_count, band_fps = "", 0, 0
            with self._state_lock:
                # The stderr reader can still receive buffered records after
                # the child exits.  Never let one of those records revive a
                # spectrum that the process watcher has already stopped.
                if self._process_exited.is_set():
                    return
                accepted = False
                if active and utterance_id > self.speech_utterance_id:
                    self.speech_active = True
                    self.speech_utterance_id = utterance_id
                    self.speech_duration_ms = duration_ms
                    self.speech_levels = levels
                    self.speech_bands = bands
                    self.speech_band_count = band_count
                    self.speech_band_fps = band_fps
                    self.speech_started_at = time.time()
                    accepted = True
                elif (
                    not active
                    and self.speech_active
                    and utterance_id == self.speech_utterance_id
                ):
                    self.speech_active = False
                    accepted = True
                if accepted:
                    # Keep state mutation and publication ordered relative to
                    # the process-exit stop event.
                    self.broker.publish(event, **payload)
            return
        self.broker.publish(event, **payload)

        if event == "input_request":
            with self._state_lock:
                shutdown_pending = self._shutdown_pending
            if shutdown_pending:
                # Do not write back to the pipe from its reader thread.  The
                # short hand-off also lets the worker finish printing its
                # prompt before a cancellation response arrives.
                threading.Timer(0.05, self._advance_shutdown).start()
                return

        if (
            event == "input_request"
            and self.initial_task
            and not self._initial_sent
        ):
            self._initial_sent = True
            task = self.initial_task
            # Let the first page paint its listening state before the queued
            # positional task begins.
            threading.Timer(0.15, lambda: self.submit(task)).start()

    def submit(
        self,
        text: str,
        display_text: str | None = None,
    ) -> tuple[bool, str]:
        if not isinstance(text, str):
            return False, "input must be text"
        encoded_text = text.encode("utf-8")
        if len(encoded_text) > _MAX_INPUT_BYTES:
            return False, "input is too large"
        if (
            not text.strip()
            and self.input_mode not in {"confirmation", "answer"}
        ):
            return False, "enter a directive first"

        with self._write_lock:
            if not self.alive:
                return False, "terminal runtime is not running"
            with self._state_lock:
                if not self.accepting_input:
                    return False, "Jarvis is still working"
                mode = self.input_mode
                self.accepting_input = False
            assert self.process is not None and self.process.stdin is not None
            wire = INPUT_PREFIX + base64.b64encode(encoded_text).decode("ascii")
            try:
                self.process.stdin.write((wire + "\n").encode("ascii"))
                self.process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                return False, "terminal runtime disconnected"

        visible = text if display_text is None else str(display_text)
        self.broker.publish("input", message=visible[:200000], mode=mode)
        self.broker.publish(
            "state",
            state="working",
            label="Executing directive" if mode == "command"
            else "Applying response",
        )
        return True, "submitted"

    def _advance_shutdown(self) -> tuple[bool, str]:
        """Cancel the current nested prompt, then quit at the main prompt."""
        if not self.alive:
            return True, "already stopped"
        with self._state_lock:
            ready = self.accepting_input
            mode = self.input_mode
        if not ready:
            return False, "shutdown queued"

        if mode == "command":
            ok, message = self.submit(":quit", display_text="")
            if ok:
                with self._state_lock:
                    self._shutdown_pending = False
            return ok, message
        if mode == "confirmation":
            return self.submit("n", display_text="")
        # Empty answers cancel /paste follow-ups and mid-task questions.
        return self.submit("", display_text="")

    def save_attachment(
        self,
        filename: str,
        mime_type: str,
        encoded: str,
    ) -> Path:
        if not mime_type.lower().startswith("image/"):
            raise ValueError("only image attachments are supported")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("attachment data is not valid base64") from exc
        if not data or len(data) > 12 * 1024 * 1024:
            raise ValueError("image must be between 1 byte and 12 MB")

        suffix = Path(filename or "").suffix.lower()
        if suffix not in _IMAGE_SUFFIXES:
            suffix = mimetypes.guess_extension(mime_type.split(";", 1)[0]) or ".png"
        if suffix == ".jpe":
            suffix = ".jpg"
        if suffix not in _IMAGE_SUFFIXES:
            suffix = ".png"

        session_dir = (
            Path(tempfile.gettempdir())
            / "jarvis-browser"
            / self.token[:12]
        )
        session_dir.mkdir(parents=True, exist_ok=True)
        target = session_dir / f"attachment-{time.time_ns()}{suffix}"
        target.write_bytes(data)
        self._attachments.append(target)
        self.broker.publish(
            "activity",
            kind="attachment",
            message=f"Attached {Path(filename).name or 'image'}",
        )
        return target

    def speech_snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "active": self.speech_active,
                "utterance_id": self.speech_utterance_id,
                "duration_ms": self.speech_duration_ms,
                "levels": list(self.speech_levels),
                "bands": self.speech_bands,
                "band_count": self.speech_band_count,
                "band_fps": self.speech_band_fps,
                "started_at": self.speech_started_at,
            }

    def request_shutdown(self) -> tuple[bool, str]:
        if not self.alive:
            return True, "already stopped"
        with self._state_lock:
            self._shutdown_pending = True
        ok, message = self._advance_shutdown()
        if ok:
            return True, "shutdown requested"
        if message == "shutdown queued":
            return True, message
        return False, message

    def request_interrupt(self) -> tuple[bool, str]:
        process = self.process
        if process is None or process.poll() is not None:
            return False, "terminal runtime is not running"
        with self._state_lock:
            if self.accepting_input:
                return False, "Jarvis is ready for the next directive"
        interrupt_signal = (
            getattr(signal, "CTRL_BREAK_EVENT", signal.SIGINT)
            if os.name == "nt"
            else signal.SIGINT
        )
        try:
            process.send_signal(interrupt_signal)
        except (OSError, ValueError) as exc:
            return False, f"could not interrupt Jarvis: {exc}"
        self.broker.publish(
            "activity",
            kind="warning",
            message="Interrupt requested for the active directive",
        )
        self.broker.publish(
            "state",
            state="working",
            label="Stopping active directive",
        )
        return True, "interrupt requested"

    def stop(self, graceful_timeout: float = 2.0) -> None:
        process = self.process
        if process is None or process.poll() is not None:
            self._cleanup_attachments()
            return
        with self._state_lock:
            self._shutdown_pending = True
        self._advance_shutdown()
        try:
            process.wait(timeout=graceful_timeout)
        except subprocess.TimeoutExpired:
            pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                process.kill()
        self._cleanup_attachments()

    def _watch_process(self) -> None:
        assert self.process is not None
        code = self.process.wait()
        self._process_exited.set()
        with self._state_lock:
            was_speaking = self.speech_active
            utterance_id = self.speech_utterance_id
            self.accepting_input = False
            self.state = "offline"
            self.speech_active = False
            if was_speaking:
                self.broker.publish(
                    "speech",
                    active=False,
                    utterance_id=utterance_id,
                    reason="session-ended",
                )
        self.broker.publish(
            "state",
            state="offline",
            label="Terminal session ended",
        )
        self.broker.publish(
            "session",
            alive=False,
            exit_code=code,
            message="Terminal runtime stopped",
        )
        self.stopped.set()

    def _cleanup_attachments(self) -> None:
        for path in self._attachments:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        self._attachments.clear()


class BrowserHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        bridge: TerminalBridge,
    ):
        self.bridge = bridge
        self.token = bridge.token
        super().__init__(server_address, BrowserRequestHandler)
        host, port = self.server_address[:2]
        self.origin = f"http://{host}:{port}"

    def handle_error(self, request: Any, client_address: Any) -> None:
        # Browsers and PowerShell's HTTP client routinely reset idle keep-alive
        # sockets while the local server is shutting down.  That is a normal
        # disconnect, not a Jarvis failure worth dumping into the terminal.
        error = sys.exc_info()[1]
        if isinstance(
            error,
            (BrokenPipeError, ConnectionAbortedError, ConnectionResetError),
        ):
            return
        super().handle_error(request, client_address)


class BrowserRequestHandler(BaseHTTPRequestHandler):
    server: BrowserHTTPServer
    protocol_version = "HTTP/1.1"

    _STATIC = {
        "/": ("index.html", "text/html; charset=utf-8"),
        "/index.html": ("index.html", "text/html; charset=utf-8"),
        "/styles.css": ("styles.css", "text/css; charset=utf-8"),
        "/app.js": ("app.js", "text/javascript; charset=utf-8"),
        "/aurora.js": ("aurora.js", "text/javascript; charset=utf-8"),
        "/grainient.js": ("grainient.js", "text/javascript; charset=utf-8"),
        "/vendor/three.min.js": ("vendor/three.min.js", "text/javascript; charset=utf-8"),
    }

    def log_message(self, _format: str, *args: Any) -> None:
        return

    def _security_headers(self) -> None:
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
            "connect-src 'self'; font-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=()",
        )
        self.send_header("Cache-Control", "no-store")

    def _send_bytes(
        self,
        status: int,
        body: bytes,
        content_type: str,
    ) -> None:
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _drain_request_body(self) -> None:
        """Consume an unread request body before replying.

        Rejecting a POST without reading its body leaves bytes sitting in the
        socket; closing on top of them makes the OS send RST, so the client
        sees a connection abort instead of the status code we just wrote.
        Bodies past the accepted ceiling are left unread on purpose - that is
        the size guard doing its job.
        """
        if getattr(self, "_body_consumed", False):
            return
        self._body_consumed = True
        try:
            remaining = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            return
        if remaining <= 0 or remaining > _MAX_ATTACHMENT_BODY:
            return
        while remaining > 0:
            chunk = self.rfile.read(min(65536, remaining))
            if not chunk:
                break
            remaining -= len(chunk)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._drain_request_body()
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _token_valid(self) -> bool:
        query_token = parse_qs(urlparse(self.path).query).get("token", [""])[0]
        supplied = self.headers.get("X-Jarvis-Token", "") or query_token
        if not supplied:
            return False
        try:
            supplied_bytes = supplied.encode("ascii")
            expected_bytes = self.server.token.encode("ascii")
        except UnicodeEncodeError:
            return False
        return hmac.compare_digest(supplied_bytes, expected_bytes)

    def _origin_valid(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        try:
            origin_bytes = origin.encode("ascii")
            expected_bytes = self.server.origin.encode("ascii")
        except UnicodeEncodeError:
            return False
        return hmac.compare_digest(origin_bytes, expected_bytes)

    def _require_api_access(self, require_origin: bool = False) -> bool:
        if not self._token_valid():
            self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
            return False
        if require_origin and not self._origin_valid():
            self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "invalid origin"})
            return False
        return True

    def do_HEAD(self) -> None:
        if urlparse(self.path).path == "/api/events":
            self._json(
                HTTPStatus.METHOD_NOT_ALLOWED,
                {"ok": False, "error": "event stream requires GET"},
            )
            return
        self.do_GET()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path in self._STATIC:
            filename, content_type = self._STATIC[path]
            target = STATIC_DIR / filename
            if not target.is_file():
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "asset missing"})
                return
            self._send_bytes(HTTPStatus.OK, target.read_bytes(), content_type)
            return

        if path == "/favicon.ico":
            self._send_bytes(HTTPStatus.NO_CONTENT, b"", "image/x-icon")
            return

        if path == "/health":
            self._json(
                HTTPStatus.OK,
                {"ok": True, "alive": self.server.bridge.alive},
            )
            return

        if path == "/api/state":
            if not self._require_api_access():
                return
            bridge = self.server.bridge
            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "alive": bridge.alive,
                    "state": bridge.state,
                    "accepting_input": bridge.accepting_input,
                    "input_mode": bridge.input_mode,
                    "input_prompt": bridge.input_prompt,
                    "speech": bridge.speech_snapshot(),
                },
            )
            return

        if path == "/api/events":
            if not self._require_api_access():
                return
            self._stream_events()
            return

        if path == "/api/sessions":
            if not self._require_api_access():
                return
            from .sessions import get_session_manager
            sm = get_session_manager()
            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "sessions": sm.list_sessions(),
                    "active_id": sm.active_session_id,
                },
            )
            return

        if path == "/api/sessions/load":
            if not self._require_api_access():
                return
            query = parse_qs(parsed.query)
            sid = query.get("id", [""])[0]
            from .sessions import get_session_manager
            sm = get_session_manager()
            session = sm.load_session(sid) if sid else None
            if session is None:
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "session not found"})
                return
            sm.active_session_id = session.id
            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "session": session.to_dict(),
                    "active_id": sm.active_session_id,
                },
            )
            return

        self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})


    def _stream_events(self) -> None:
        subscriber, history = self.server.bridge.broker.subscribe()
        try:
            last_id_raw = self.headers.get("Last-Event-ID", "0")
            try:
                last_id = max(0, int(last_id_raw))
            except ValueError:
                last_id = 0

            self.send_response(HTTPStatus.OK)
            self._security_headers()
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            for record in history:
                if int(record["id"]) > last_id:
                    self._write_sse(record)

            while True:
                try:
                    record = subscriber.get(timeout=12)
                    self._write_sse(record)
                except queue.Empty:
                    self.wfile.write(b": neural-link heartbeat\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.server.bridge.broker.unsubscribe(subscriber)

    def _write_sse(self, record: dict[str, Any]) -> None:
        data = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        wire = f"id: {record['id']}\ndata: {data}\n\n".encode("utf-8")
        self.wfile.write(wire)
        self.wfile.flush()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path not in {
            "/api/input",
            "/api/attachment",
            "/api/interrupt",
            "/api/shutdown",
            "/api/sessions/new",
            "/api/sessions/delete",
        }:
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            return
        if not self._require_api_access(require_origin=True):
            return
        if self.headers.get_content_type() != "application/json":
            self._json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"ok": False, "error": "application/json required"},
            )
            return

        limit = (
            _MAX_ATTACHMENT_BODY
            if path == "/api/attachment"
            else _MAX_INPUT_BODY
        )
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > limit:
            self._json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"ok": False, "error": "request is too large"},
            )
            return
        try:
            self._body_consumed = True
            body = self.rfile.read(length)
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "invalid JSON"},
            )
            return
        if not isinstance(payload, dict):
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "JSON object required"},
            )
            return

        if path == "/api/sessions/new":
            from .sessions import get_session_manager
            sm = get_session_manager()
            title = str(payload.get("title") or "New Session").strip() or "New Session"
            session = sm.create_session(title)
            self.server.bridge.broker.publish("session_list", sessions=sm.list_sessions(), active_id=session.id)
            self._json(
                HTTPStatus.OK,
                {"ok": True, "session": session.to_dict(), "active_id": session.id},
            )
            return

        if path == "/api/sessions/delete":
            sid = str(payload.get("id", "")).strip()
            from .sessions import get_session_manager
            sm = get_session_manager()
            ok = sm.delete_session(sid) if sid else False
            if ok:
                self.server.bridge.broker.publish("session_list", sessions=sm.list_sessions(), active_id=sm.active_session_id)
            self._json(
                HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST,
                {"ok": ok, "active_id": sm.active_session_id},
            )
            return

        if path == "/api/input":
            ok, message = self.server.bridge.submit(
                payload.get("text", ""),
                display_text=payload.get("display_text"),
            )
            self._json(
                HTTPStatus.OK if ok else HTTPStatus.CONFLICT,
                {"ok": ok, "message": message, "error": None if ok else message},
            )
            return


        if path == "/api/attachment":
            try:
                saved = self.server.bridge.save_attachment(
                    str(payload.get("name", "image.png")),
                    str(payload.get("type", "")),
                    str(payload.get("data", "")),
                )
            except (ValueError, OSError) as exc:
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {"ok": False, "error": str(exc)},
                )
                return
            self._json(
                HTTPStatus.OK,
                {"ok": True, "path": str(saved), "name": saved.name},
            )
            return

        if path == "/api/interrupt":
            ok, message = self.server.bridge.request_interrupt()
            self._json(
                HTTPStatus.OK if ok else HTTPStatus.CONFLICT,
                {"ok": ok, "message": message, "error": None if ok else message},
            )
            return

        ok, message = self.server.bridge.request_shutdown()
        self._json(
            HTTPStatus.OK if ok else HTTPStatus.CONFLICT,
            {"ok": ok, "message": message, "error": None if ok else message},
        )


def run_browser(
    child_args: list[str] | None = None,
    initial_task: str | None = None,
) -> int:
    """Start the loopback UI, open it, and wait for the terminal REPL."""
    if not all((STATIC_DIR / name).is_file()
               for name in ("index.html", "styles.css", "app.js")):
        print("Jarvis browser UI assets are missing.", file=sys.stderr)
        return 1

    token = secrets.token_urlsafe(32)
    bridge = TerminalBridge(
        child_args=child_args,
        initial_task=initial_task,
        token=token,
    )
    try:
        configured_port = int(os.getenv("JARVIS_BROWSER_PORT", "0") or 0)
    except ValueError:
        configured_port = 0

    try:
        server = BrowserHTTPServer((HOST, configured_port), bridge)
    except OSError as exc:
        print(f"Could not start Jarvis browser interface: {exc}", file=sys.stderr)
        return 1

    server_thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.2},
        name="jarvis-browser-http",
        daemon=True,
    )
    server_thread.start()

    try:
        bridge.start()
    except Exception as exc:
        server.shutdown()
        server.server_close()
        print(f"Could not start Jarvis terminal runtime: {exc}", file=sys.stderr)
        return 1

    port = int(server.server_address[1])
    base_url = f"http://{HOST}:{port}/"
    browser_url = f"{base_url}#token={quote(token)}"
    print()
    print("  JARVIS browser interface online")
    print(f"  {browser_url}")
    print("  Keep this terminal open. Press Ctrl+C here to end the session.")
    print()

    if os.getenv("JARVIS_BROWSER_NO_OPEN", "").lower() not in {"1", "true", "yes"}:
        try:
            opened = webbrowser.open(browser_url, new=2)
            if not opened:
                print(f"Open this address in your browser: {browser_url}")
        except Exception:
            print(f"Open this address in your browser: {browser_url}")

    try:
        while not bridge.stopped.wait(0.5):
            pass
    except KeyboardInterrupt:
        print("\nStopping Jarvis browser session...")
        bridge.stop()
    finally:
        server.shutdown()
        server.server_close()
        bridge.stop()
        server_thread.join(timeout=1.0)

    return int(bridge.process.returncode or 0) if bridge.process else 0
