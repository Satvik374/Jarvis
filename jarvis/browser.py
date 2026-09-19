"""Local browser frontend for Jarvis.

The page is intentionally a thin interface over the existing terminal REPL:
the REPL runs in a child Python process, stdin carries directives, stdout
remains the terminal transcript, and structured stderr events drive the UI.
No web framework or internet connection is required.
"""

from __future__ import annotations

import asyncio
import base64
import codecs
from collections import deque
import hmac
from http import HTTPStatus
import itertools
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

import websockets

from .browser_worker import EVENT_PREFIX
from .config import ROOT
from .utils import logging as log


HOST = "127.0.0.1"
STATIC_DIR = Path(__file__).resolve().parent / "browser_ui"
INPUT_PREFIX = "__JARVIS_BROWSER_INPUT64__:"
TOOL_PREFIX = "__JARVIS_BROWSER_TOOL__:"
#: How much of a direct action's output is handed back to the caller. Fish caps
#: a client-tool result around 60 KB once JSON-encoded, and the model reads the
#: whole thing every turn, so a big result is trimmed with a marker.
_MAX_TOOL_RESULT = 24000

#: Upstream statuses that mean something specific to the caller rather than
#: "the gateway failed". Anything else becomes a 502.
_PASSTHROUGH_STATUS = {401, 402, 403, 404, 409, 429}
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
#: A stderr line that looks like a failure worth showing the user later.
_ERROR_MARK_RE = re.compile(
    r"(?i)\b(error|traceback|exception|failed|refused|denied|no such|cannot)\b"
)
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
        interface_mode: str = "console",
        live_voice: bool = False,
    ):
        self.child_args = list(child_args or [])
        self.initial_task = initial_task
        self.token = token or secrets.token_urlsafe(32)
        self.interface_mode = (
            "remote-agent" if interface_mode == "remote-agent" else "console"
        )
        self.live_voice = bool(live_voice)
        self.live_voice_active = bool(live_voice)
        self.launch_cwd = Path.cwd()
        self.broker = EventBroker()
        self.process: subprocess.Popen[bytes] | None = None
        self.stopped = threading.Event()
        self.accepting_input = False
        #: Enough about the child to explain *why* the link is down. Without
        #: this the page can only say "OFFLINE", which is the difference
        #: between a user who knows what to do and one who sees a dead UI.
        self.started_at: float | None = None
        self.exit_code: int | None = None
        self.last_error = ""
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
        self.speech_audio = ""
        self.speech_wav_bytes = b""
        self._initial_sent = False
        self._shutdown_pending = False
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._process_exited = threading.Event()
        self._attachments: list[Path] = []
        # Direct action execution: the HTTP thread parks on an Event while the
        # stderr reader resolves it from the child's "tool_result" event.
        self._tool_lock = threading.Lock()
        self._tool_waiters: dict[str, threading.Event] = {}
        self._tool_results: dict[str, dict[str, Any]] = {}
        self._tool_counter = itertools.count(1)

    def set_live_voice_active(self, active: bool) -> None:
        """Update live voice mode state to mute/unmute the Communication Agent."""
        self.live_voice_active = bool(active)
        try:
            from .utils import voice
            voice.set_live_mode_active(bool(active))
        except Exception:
            pass

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def worker_snapshot(self) -> dict[str, Any]:
        """What the UI needs to explain a missing or dead terminal runtime.

        The page shows a diagnosis instead of a bare "OFFLINE": an exit code, how
        long the runtime ran, and the last error line it printed.
        """
        process = self.process
        code = self.exit_code
        if process is not None:
            polled = process.poll()
            if polled is not None:
                code = polled
        started = self.started_at
        return {
            "alive": self.alive,
            "pid": getattr(process, "pid", None),
            "exit_code": code,
            "started_at": started,
            "uptime_seconds": round(time.time() - started, 1) if started else None,
            "last_error": self.last_error,
            "interface_mode": self.interface_mode,
            "stopping": self.stopped.is_set(),
        }

    def start(self) -> None:
        if self.process is not None:
            raise RuntimeError("terminal bridge already started")
        self.started_at = time.time()
        self.exit_code = None
        self.last_error = ""

        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        env.setdefault("PYTHONUTF8", "1")
        # The shared live flag tracks later browser toggles; a startup-only
        # environment override would keep the child muted after Live exits.
        env.pop("JARVIS_LIVE_MODE", None)
        self.set_live_voice_active(self.live_voice_active)
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
            interface_mode=self.interface_mode,
            message=(
                "Remote agent runtime started"
                if self.interface_mode == "remote-agent"
                else "Local terminal runtime started"
            ),
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
                    if _ERROR_MARK_RE.search(line):
                        # Keep the last failure-looking line: it is the best
                        # available answer to "why did my runtime die?".
                        self.last_error = _ANSI_RE.sub("", line).strip()[:300]
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
        if event == "tool_result":
            # Resolves a parked execute_tool() call. Never published as a plain
            # event: the HTTP response is the consumer.
            self._resolve_tool_result(payload)
            return
        if event in {"state", "input_request"}:
            with self._state_lock:
                if self._process_exited.is_set():
                    return
                if event == "state":
                    self.state = str(payload.get("state", self.state))
                else:
                    self.accepting_input = True
                    self.input_mode = str(payload.get("mode", "command"))
                    self.input_prompt = str(payload.get("prompt", ""))
                # Publish before the exit watcher can announce offline.
                self.broker.publish(event, **payload)
        elif event == "speech":
            if getattr(self, "live_voice_active", False):
                # When Live Voice Mode is active, all speech is handled exclusively by gpt-realtime.
                # The Communication Agent is silenced and must not speak.
                return
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
            raw_audio = payload.get("audio", "")
            audio = raw_audio if isinstance(raw_audio, str) else ""
            wav_bytes = b""
            if audio.startswith("data:audio/wav;base64,"):
                try:
                    wav_bytes = base64.b64decode(audio.split(",", 1)[1])
                except Exception:
                    wav_bytes = b""
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
                    self.speech_audio = audio
                    self.speech_wav_bytes = wav_bytes
                    accepted = True
                elif (
                    not active
                    and self.speech_active
                    and utterance_id == self.speech_utterance_id
                ):
                    self.speech_active = False
                    self.speech_audio = ""
                    accepted = True
                if accepted:
                    # Keep state mutation and publication ordered relative to
                    # the process-exit stop event.
                    self.broker.publish(event, **payload)
            return
        else:
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

    def execute_tool(
        self,
        name: str,
        args: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Run one action directly in the terminal runtime and return its result.

        Used by the voice agent's direct tools, which skip the perceive/think/act
        loop entirely. Only ever dispatched while the runtime is idle at a
        prompt, so a direct call cannot race the agentic loop over the desktop.
        """
        from .live import direct_tools

        if not isinstance(name, str) or not name:
            return {"ok": False, "error": "tool name is required"}
        if args is not None and not isinstance(args, dict):
            return {"ok": False, "error": "args must be an object"}
        if not direct_tools.is_direct(name):
            return {
                "ok": False,
                "error": f"'{name}' is not an available direct tool",
            }
        if timeout is None:
            timeout = float(direct_tools.TOOL_TIMEOUT_SECONDS)

        with self._state_lock:
            if not self.alive:
                return {"ok": False, "error": "terminal runtime is not running"}
            if not self.accepting_input:
                return {
                    "ok": False,
                    "error": (
                        "Jarvis is busy executing a task; direct actions are "
                        "only available while it is idle"
                    ),
                }

        call_id = f"direct-{next(self._tool_counter)}"
        waiter = threading.Event()
        with self._tool_lock:
            self._tool_waiters[call_id] = waiter

        wire = TOOL_PREFIX + json.dumps(
            {"call_id": call_id, "tool": name, "args": args or {}}
        )
        try:
            with self._write_lock:
                if not self.alive or self.process is None or self.process.stdin is None:
                    raise BrokenPipeError("terminal runtime unavailable")
                self.process.stdin.write((wire + "\n").encode("ascii"))
                self.process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            with self._tool_lock:
                self._tool_waiters.pop(call_id, None)
            return {"ok": False, "error": "terminal runtime disconnected"}

        delivered = waiter.wait(max(1.0, float(timeout)))
        with self._tool_lock:
            self._tool_waiters.pop(call_id, None)
            result = self._tool_results.pop(call_id, None)
        if not delivered or result is None:
            return {
                "ok": False,
                "error": f"{name} did not finish within {float(timeout):.0f}s",
            }
        return result

    def _resolve_tool_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Hand a child ``tool_result`` event to the waiting HTTP thread."""
        call_id = str(payload.get("call_id", ""))
        text = str(payload.get("result", ""))
        if len(text) > _MAX_TOOL_RESULT:
            text = (
                text[:_MAX_TOOL_RESULT]
                + f"\n[output truncated at {_MAX_TOOL_RESULT} characters]"
            )
        result = {
            "ok": bool(payload.get("ok")),
            "result": text,
            "error": str(payload.get("error", "")),
        }
        with self._tool_lock:
            waiter = self._tool_waiters.get(call_id)
        if waiter is None:
            # No waiter: either a late answer to a timed-out call or a request
            # from an older page. Publish it so it is still visible in the log.
            self.broker.publish("tool", tool=str(payload.get("tool", "")), **result)
            return result
        self._tool_results[call_id] = result
        waiter.set()
        return result

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
                "audio": self.speech_audio,
            }

    def request_shutdown(self) -> tuple[bool, str]:
        if not self.alive:
            return True, "already stopped"
        if self.interface_mode == "remote-agent":
            # A remote agent normally waits inside a relay long-poll and has
            # no command prompt at which to submit :quit.  Interrupting that
            # opt-in loop is its graceful shutdown path.
            process = self.process
            assert process is not None
            interrupt_signal = (
                getattr(signal, "CTRL_BREAK_EVENT", signal.SIGINT)
                if os.name == "nt"
                else signal.SIGINT
            )
            try:
                process.send_signal(interrupt_signal)
            except (OSError, ValueError) as exc:
                return False, f"could not stop remote agent: {exc}"
            self.broker.publish(
                "activity",
                kind="warning",
                message="Remote agent shutdown requested",
            )
            return True, "remote agent shutdown requested"
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
        self.set_live_voice_active(False)

    def _watch_process(self) -> None:
        assert self.process is not None
        code = self.process.wait()
        self.exit_code = code
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
            label=(
                "Remote agent session ended"
                if self.interface_mode == "remote-agent"
                else "Terminal session ended"
            ),
        )
        self.broker.publish(
            "session",
            alive=False,
            exit_code=code,
            interface_mode=self.interface_mode,
            message=(
                "Remote agent runtime stopped"
                if self.interface_mode == "remote-agent"
                else "Terminal runtime stopped"
            ),
        )
        self.stopped.set()

    def interface_snapshot(self) -> dict[str, Any]:
        """Return display-safe metadata for the active browser surface."""
        snapshot: dict[str, Any] = {
            "mode": self.interface_mode,
            "pairings": [],
            "unattended": False,
        }
        if self.interface_mode != "remote-agent":
            return snapshot
        try:
            from .config import load_config
            from .remote import PairingStore

            cfg = load_config()
            snapshot["unattended"] = (
                "--remote-allow-unattended" in self.child_args
                or not cfg.remote.require_confirmation
            )
            for pairing in PairingStore(cfg.remote.state_dir).list(role="agent"):
                endpoint = urlparse(pairing.endpoint)
                snapshot["pairings"].append({
                    "label": pairing.label,
                    "peer_name": pairing.peer_name,
                    "local_name": pairing.local_name,
                    "trusted": pairing.trusted,
                    "relay": endpoint.hostname or endpoint.netloc or "configured relay",
                })
        except Exception:
            # Status metadata must never interfere with the encrypted worker.
            pass
        return snapshot

    def _cleanup_attachments(self) -> None:
        for path in self._attachments:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        self._attachments.clear()


class LiveSocketRelay:
    """Live voice for the browser, with the Gemini API key kept on this machine.

    Why the page does not simply open the socket itself
    --------------------------------------------------
    A Live API key is long-lived, so putting it in a page that is served from a
    loopback port is a needless widening of the blast radius. Google's documented
    answer for browsers is an ephemeral token, and that was tried first: the token
    mints fine, but the Live socket rejects every documented transport for it -
    ``1008 ... unregistered callers`` with ``?access_token=`` on v1alpha and
    v1beta, and with ``Authorization: Token``. Until that changes, the page talks
    to this relay instead and the relay talks to Google.

    What that buys, beyond hiding the key
    -------------------------------------
    * The session is configured by the server, so a page cannot restate the
      system prompt, invent tools, or widen its own capabilities.
    * The Google Search downgrade needs the account's behaviour, which only this
      side can observe: a refused search tool and a spent Live allowance produce
      the *same* generic quota error, so the relay retries the identical setup
      without the search tool to find out which one it was, and tells the page.
    * A session the server ended (goAway, model fallback) is retried here, so the
      page keeps one socket open rather than reimplementing the policy.

    Audio still originates and is played in the browser tab; the hop through
    loopback is the only difference from talking to Google directly.
    """

    #: How long to wait for setupComplete before treating the handshake as dead.
    SETUP_TIMEOUT = 25.0

    #: Page messages buffered while the upstream session is being established.
    #: The page starts the microphone as soon as it opens the socket, so dropping
    #: the first seconds of speech would be the alternative.
    _PENDING_LIMIT = 400

    def __init__(self, bridge: TerminalBridge, origin: str = "", host: str = HOST):
        self.bridge = bridge
        self.origin = origin
        self.host = host
        self.port = 0
        self.sessions = 0
        self.last_error = ""
        self.notice = ""
        #: Set once a handshake proved Google Search grounding is refused for
        #: this key, so later sessions do not spend a handshake relearning it.
        self.grounding_blocked = ""
        self.grounding_active = False
        #: The model that actually answered `setupComplete`, once one has. The
        #: page shows this, and the configured name is not always the one that
        #: ran: a retired name in .env is mapped to its replacement, and a
        #: refused model falls through to the next candidate.
        self.model = ""
        self._server: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._started = threading.Event()

    # -- lifecycle --------------------------------------------------------- #

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}/live" if self.port else ""

    def start(self) -> bool:
        """Serve the relay on an ephemeral loopback port."""
        if self.port:
            return True

        def _thread_target() -> None:
            loop = asyncio.new_event_loop()
            self._loop = loop
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self._serve())
            except Exception as exc:  # pragma: no cover - startup failure
                log.warn(f"Live voice relay could not start: {exc}")
                self.last_error = str(exc)
                self._started.set()
            finally:
                loop.close()

        self._thread = threading.Thread(
            target=_thread_target, daemon=True, name="jarvis-live-relay"
        )
        self._thread.start()
        if not self._started.wait(timeout=5.0):
            return False
        return bool(self.port)

    def stop(self) -> None:
        if self._loop is None or not self.port:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
        except Exception:
            pass
        self.port = 0

    async def _serve(self) -> None:
        async with websockets.serve(self._handler, self.host, 0, max_size=None) as server:
            self._server = server
            self.port = int(server.sockets[0].getsockname()[1])
            self._started.set()
            await asyncio.Future()

    async def _shutdown(self) -> None:
        server = self._server
        if server is not None:
            server.close()
            await server.wait_closed()

    # -- auth -------------------------------------------------------------- #

    def _request_ok(self, ws: Any) -> bool:
        """Only our own page may use this socket.

        The token is the same one that guards the HTTP API, and it arrives in the
        query string because a browser cannot set WebSocket headers. ``Origin``
        is checked the way the HTTP handler checks it: absent is tolerated (a
        non-browser client), present must match the interface's own origin.
        """
        request = getattr(ws, "request", None)
        target = str(getattr(request, "path", "/") or "/")
        parsed = urlparse(target)
        if parsed.path.rstrip("/") != "/live":
            return False
        supplied = (parse_qs(parsed.query).get("token") or [""])[0]
        try:
            if not supplied or not hmac.compare_digest(
                supplied.encode("ascii"), self.bridge.token.encode("ascii")
            ):
                return False
        except UnicodeEncodeError:
            return False
        origin = (getattr(request, "headers", None) or {}).get("Origin")
        if origin and self.origin and origin != self.origin:
            return False
        return True

    # -- describe ---------------------------------------------------------- #

    def describe(self, cfg: Any = None) -> dict[str, Any]:
        """The live-voice block the page reads from ``/api/live/config``."""
        live = getattr(cfg, "live_voice", None)
        from .live import gemini_live, readiness

        key = readiness.gemini_api_key(cfg) if cfg is not None else ""
        mode = str(getattr(live, "google_search", "auto") or "auto")
        notice = self.notice or self.grounding_blocked
        return {
            "ready": bool(self.port and key),
            "ws_url": self.url,
            # Before a session opens this is the model it *will* open, which is
            # not the configured one once a model has been measured ignoring
            # audio for this key - the page shows this name to the user.
            "model": self.model
            or (
                gemini_live.preferred_model(live, key)
                if live is not None
                else gemini_live.DEFAULT_MODEL
            ),
            "voice": getattr(live, "voice_name", "") or gemini_live.DEFAULT_VOICE,
            "media_resolution": gemini_live.media_resolution(
                getattr(live, "media_resolution", "medium")
            ),
            "sample_rate_in": gemini_live.PCM_IN_RATE,
            "sample_rate_out": gemini_live.PCM_OUT_RATE,
            "google_search": {
                "mode": mode,
                "active": bool(self.grounding_active),
                "notice": notice,
            },
            "screen_share": {
                "enabled": bool(getattr(live, "screen_share", True)),
                "interval": float(getattr(live, "screen_share_interval", 1.0) or 1.0),
                "max_dim": int(getattr(live, "screen_share_max_dim", 1024) or 1024),
                "quality": int(getattr(live, "screen_share_quality", 60) or 60),
            },
            "sessions": self.sessions,
            "error": self.last_error,
            "notice": notice,
        }

    # -- the session ------------------------------------------------------- #

    async def _handler(self, client: Any) -> None:
        if not self._request_ok(client):
            await client.close(code=1008, reason="unauthorized")
            return
        self.sessions += 1
        try:
            await self._relay(client)
        except Exception as exc:  # pragma: no cover - transport failure
            log.debug(f"Live voice relay session ended: {exc}")
        finally:
            try:
                await client.close()
            except Exception:
                pass

    async def _relay(self, client: Any) -> None:
        from .config import load_config
        from .live import gemini_live, readiness

        cfg = load_config()
        live = cfg.live_voice
        key = readiness.gemini_api_key(cfg)
        if not key:
            await self._notice(
                client,
                "no_key",
                "Live voice needs a Gemini API key.",
                remedy=(
                    "create a free key at https://aistudio.google.com/apikey, then set "
                    "JARVIS_LIVE_API_KEY in .env or store it with ':secret set "
                    "JARVIS_LIVE_API_KEY <key>' in the Jarvis console"
                ),
            )
            return

        # Grounding is offered once per key, not once per session: the downgrade
        # is remembered so a user whose plan has no search grounding does not pay
        # a rejected handshake every time they press the button.
        grounding = gemini_live.grounding_wanted(live)
        if grounding and self.grounding_blocked:
            grounding = False

        candidates = gemini_live.hearing_candidates(live, key)
        pending: deque[Any] = deque(maxlen=self._PENDING_LIMIT)
        # Model and attempt are counted separately on purpose. Dropping the
        # search tool is a retry of the *same* model, so it must not also walk
        # down the model list - that turned one refused tool into a session on a
        # different model than the one that was configured.
        model_idx = 0
        attempts = 0
        max_attempts = len(candidates) + 3

        while attempts < max_attempts:
            attempts += 1
            model = candidates[model_idx % len(candidates)]
            upstream_uri = f"{gemini_live.GEMINI_LIVE_WS}?key={key}"
            try:
                async with websockets.connect(
                    upstream_uri,
                    open_timeout=15.0,
                    ping_interval=None,
                    ping_timeout=None,
                    max_size=None,
                ) as upstream:
                    setup = gemini_live.build_setup(
                        live,
                        model=model,
                        system_instruction=self._system_prompt(),
                        google_search=grounding,
                    )
                    await upstream.send(json.dumps(setup))
                    try:
                        first = json.loads(
                            await asyncio.wait_for(upstream.recv(), timeout=self.SETUP_TIMEOUT)
                        )
                    except asyncio.TimeoutError:
                        await self._notice(client, "timeout", "Gemini Live did not answer the handshake.")
                        return

                    if not ("setupComplete" in first or "setup_complete" in first):
                        # The server rejected the setup. Quota is ambiguous (see
                        # the class docstring); a model error means the next
                        # candidate is worth trying.
                        reason = json.dumps(first)[:300]
                        refusal = gemini_live.quota_refusal(reason)
                        if grounding and refusal:
                            grounding = False
                            self.grounding_blocked = reason
                            self.notice = (
                                "Google Search grounding was refused for this key (it needs "
                                "grounding quota, usually a paid plan), so live voice is running "
                                "without web search."
                            )
                            readiness.remember_failure("gemini_grounding", self.notice)
                            log.warn(f"Gemini Live refused the search tool for {model}; retrying without it.")
                            continue
                        if refusal:
                            readiness.remember_failure("gemini", f"Gemini Live refused the session: {reason}")
                            await self._notice(
                                client,
                                "quota",
                                "Gemini Live refused the session: the API key's Live quota is spent.",
                                remedy="check the plan and billing on the key's Google Cloud / AI Studio project",
                            )
                            return
                        log.warn(f"Gemini Live rejected the setup for {model}: {reason}")
                        model_idx += 1
                        continue

                    self.grounding_active = bool(grounding)
                    self.model = model
                    self.last_error = ""
                    await self._status(client, grounding=bool(grounding), notice=self.notice)
                    deaf = await self._pump(client, upstream, pending, live, model, grounding)
                    if deaf:
                        # The handshake was accepted and the model then ignored
                        # the microphone entirely. Silence looks exactly like a
                        # user who has not spoken, so nothing else in this
                        # session will ever report it - walking down the model
                        # list is what makes voice work at all. See
                        # ``gemini_live._AUDIO_DEAF`` for what was measured.
                        gemini_live.mark_audio_deaf(
                            model,
                            "accepted the live session and ignored audio input",
                            key=key,
                        )
                        following = candidates[(model_idx + 1) % len(candidates)]
                        log.warn(
                            f"Gemini Live: {model} accepted the session and heard nothing; "
                            f"switching to {following}"
                        )
                        await self._notice(
                            client,
                            "model_deaf",
                            f"{model} accepted the live session but did not hear your "
                            f"microphone, so the session was moved to {following}.",
                            remedy=(
                                "set live_voice.model in config.yaml to a live model that "
                                "accepts audio input"
                            ),
                        )
                        model_idx += 1
                        continue
                    return
            except websockets.exceptions.ConnectionClosed as exc:
                reason = str(exc)
                if grounding and gemini_live.quota_refusal(reason):
                    grounding = False
                    self.grounding_blocked = reason
                    self.notice = (
                        "Google Search grounding was refused for this key, so live voice is "
                        "running without web search."
                    )
                    readiness.remember_failure("gemini_grounding", self.notice)
                    log.warn(f"Gemini Live refused the search tool for {model}; retrying without it.")
                    continue
                if gemini_live.quota_refusal(reason):
                    readiness.remember_failure("gemini", f"Gemini Live quota refused: {reason}")
                    await self._notice(client, "quota", "Gemini Live quota refused the session.",
                                       remedy="check the plan and billing for this API key")
                    return
                self.last_error = reason
                log.warn(f"Gemini Live connection failed ({model}): {reason}")
                model_idx += 1
                continue
            except Exception as exc:
                self.last_error = str(exc)
                log.warn(f"Gemini Live relay could not open a session: {exc}")
                model_idx += 1
                await asyncio.sleep(0.3)
                continue

        readiness.remember_failure("gemini", self.last_error or "no live model accepted the session")
        await self._notice(
            client,
            "unavailable",
            "No Gemini Live session could be opened.",
            remedy=self.last_error or "try another live model in config.yaml",
        )

    async def _pump(
        self,
        client: Any,
        upstream: Any,
        pending: deque[Any],
        live: Any,
        model: str,
        grounding: bool,
    ) -> bool:
        """Relay the conversation until either end closes.

        Returns True when the session was ended by the silence watchdog - the
        model was sent audible audio and produced nothing - so the caller can
        try the next model instead of leaving the user with a dead session.
        """
        log.ok(f"Live voice session open on {model}{'' if grounding else ' (without Google Search)'}")

        # The page cannot tell "the user has not spoken" from "the model is not
        # answering", and the second one looks like a healthy session. Both
        # numbers below are counted to tell them apart when this session ends.
        audible_frames = 0
        model_messages = 0
        last_audible_at = 0.0

        async def page_to_upstream() -> None:
            nonlocal audible_frames, last_audible_at
            while True:
                raw = await client.recv()
                try:
                    message = json.loads(raw)
                except Exception:
                    continue
                audible = _audible(raw)
                if audible:
                    last_audible_at = asyncio.get_event_loop().time()
                if audible_frames < _AUDIBLE_FRAME_LIMIT:
                    audible_frames += audible
                if isinstance(message, dict) and "setup" in message:
                    # The session is the server's to configure; a page that could
                    # restate it could also give itself tools it does not have.
                    await self._notice(
                        client, "setup_refused",
                        "The live session configuration comes from the server, not the page.",
                    )
                    continue
                await upstream.send(raw)

        async def upstream_to_page() -> None:
            nonlocal model_messages
            while True:
                raw = await upstream.recv()
                # The Live endpoint packs its JSON into *binary* frames, so
                # ``recv()`` hands back ``bytes``. Forwarding those to the page
                # unchanged makes every message arrive there as a Blob, and
                # ``JSON.parse(blob)`` throws - which the page catches and
                # ignores, dropping the setup, the transcripts and the audio of
                # an otherwise working session. Everything upstream sends is
                # JSON, so it is decoded here and the page only ever sees text.
                if isinstance(raw, (bytes, bytearray)):
                    try:
                        raw = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        log.debug("Live voice relay: undecodable frame from upstream")
                        continue
                try:
                    message = json.loads(raw)
                except Exception:
                    continue
                if isinstance(message, dict) and (
                    message.get("serverContent") or message.get("server_content")
                    or message.get("toolCall") or message.get("tool_call")
                ):
                    model_messages += 1
                go_away = message.get("goAway") or message.get("go_away")
                if isinstance(go_away, dict):
                    await self._notice(
                        client, "go_away",
                        "Gemini will close this session shortly; it will continue on a new one.",
                    )
                await client.send(raw)

        async def ignore_watchdog() -> None:
            """End a session that was spoken to, then went quiet, and said nothing.

            The quiet gap is what makes this a verdict rather than a guess. The
            model only ends a turn - and so only transcribes or answers - after
            the user stops, so a microphone that never stops (a looping test
            clip, an open line, background noise) produces exactly the same
            silence from a *working* model as from one that ignores audio. Once
            the user has paused and the model is still blank, there is nothing
            left to wait for.
            """
            while True:
                await asyncio.sleep(_SILENCE_WATCHDOG_SECONDS)
                quiet_for = asyncio.get_event_loop().time() - last_audible_at
                if (
                    audible_frames >= _AUDIBLE_FRAME_LIMIT
                    and not model_messages
                    and last_audible_at
                    and quiet_for >= _QUIET_BEFORE_VERDICT_SECONDS
                ):
                    return

        watchdog = asyncio.create_task(ignore_watchdog())
        tasks = [
            asyncio.create_task(page_to_upstream()),
            asyncio.create_task(upstream_to_page()),
            watchdog,
        ]
        # Anything the page sent while the handshake was in flight is delivered
        # now, so the first words of a sentence are not lost.
        while pending:
            try:
                await upstream.send(pending.popleft())
            except Exception:
                break
        try:
            done, waiting = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
        deaf = watchdog in done
        silent = "" if deaf else _silent_session_notice(audible_frames, model_messages)
        if silent:
            log.warn(
                f"Live voice: {audible_frames} frames of audible audio reached {model} "
                "and the model produced no output at all."
            )
            await self._notice(client, "no_model_output", silent, remedy=(
                "run: python run.py --check --live-check"
            ))
        for task in done:
            if task is watchdog:
                continue
            exc = task.exception()
            if exc:
                self.last_error = str(exc)
                log.debug(f"Live voice relay ended: {exc}")
        return deaf

    @staticmethod
    def _system_prompt() -> str:
        from .live import prompts

        try:
            return prompts.build_live_voice_system_prompt()
        except Exception:
            return "You are Jarvis, a concise voice assistant."

    async def _notice(self, client: Any, code: str, message: str, remedy: str = "") -> None:
        """Tell the page why the session is not running, in words it can show."""
        log.warn(message)
        await self._send(client, {"jarvisNotice": {"code": code, "message": message, "remedy": remedy}})

    async def _status(self, client: Any, *, grounding: bool, notice: str = "") -> None:
        await self._send(client, {"jarvisStatus": {"grounding": bool(grounding), "notice": notice}})

    @staticmethod
    async def _send(client: Any, payload: dict[str, Any]) -> None:
        try:
            await client.send(json.dumps(payload))
        except Exception:
            pass


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
        #: The loopback WebSocket relay that holds the Gemini key while the page
        #: streams its microphone. Attached by run_browser; a server built
        #: directly in a test simply has no relay, and the page is told so.
        self.live_relay: LiveSocketRelay | None = None

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
        "/blob.js": ("blob.js", "text/javascript; charset=utf-8"),
        "/grainient.js": ("grainient.js", "text/javascript; charset=utf-8"),
        "/hologram.js": ("hologram.js", "text/javascript; charset=utf-8"),
        "/vendor/three.min.js": ("vendor/three.min.js", "text/javascript; charset=utf-8"),
        "/vendor/fish-agent-client.esm.js": (
            "vendor/fish-agent-client.esm.js",
            "text/javascript; charset=utf-8",
        ),
    }

    def log_message(self, _format: str, *args: Any) -> None:
        return

    def _security_headers(self) -> None:
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' 'unsafe-eval' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https:; img-src 'self' data: blob:; "
            "media-src 'self' data: blob:; "
            # https://api.fish.audio is needed only for Fish Agents public-agent
            # sessions (no key in the browser); the SDK talks to it directly.
            "connect-src 'self' ws: wss: https://api.fish.audio; "
            "font-src 'self' https: data:; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Permissions-Policy",
            "camera=(self), microphone=(self), geolocation=(), payment=()",
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
            interface = (
                bridge.interface_snapshot()
                if hasattr(bridge, "interface_snapshot")
                else {"mode": getattr(bridge, "interface_mode", "console"),
                      "pairings": [], "unattended": False}
            )
            #: Why the runtime died, for a page that would otherwise only know it did.
            worker = (
                bridge.worker_snapshot() if hasattr(bridge, "worker_snapshot") else {}
            )
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
                    "interface": interface,
                    "worker": worker,
                },
            )
            return

        if path == "/api/events":
            if not self._require_api_access():
                return
            self._stream_events()
            return

        if path == "/api/live/config":
            self._handle_live_config()
            return

        if path == "/api/speech/audio.wav":
            if not self._require_api_access():
                return
            lock = getattr(self.server.bridge, "_state_lock", None)
            if lock is not None:
                with lock:
                    wav_bytes = getattr(self.server.bridge, "speech_wav_bytes", b"")
            else:
                wav_bytes = getattr(self.server.bridge, "speech_wav_bytes", b"")
            if not wav_bytes:
                self._send_bytes(HTTPStatus.NO_CONTENT, b"", "audio/wav")
                return
            self._send_bytes(HTTPStatus.OK, wav_bytes, "audio/wav")
            return

        if path == "/api/vision/frame":
            if not self._require_api_access():
                return
            qs = parse_qs(parsed.query)
            source = qs.get("source", ["both"])[0]
            try:
                from .perception import capture_live_frame
                frame_bytes = capture_live_frame(source=source)
                self._send_bytes(HTTPStatus.OK, frame_bytes, "image/jpeg")
            except Exception as exc:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
            return

        if path == "/api/vision/analyze":
            if not self._require_api_access():
                return
            qs = parse_qs(parsed.query)
            prompt = qs.get("prompt", ["What do you see?"])[0]
            source = qs.get("source", ["both"])[0]
            try:
                from .perception import see
                analysis = see(prompt=prompt, source=source)
                self._json(HTTPStatus.OK, {"ok": True, "source": source, "prompt": prompt, "analysis": analysis})
            except Exception as exc:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
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

        if path == "/api/skills":
            if not self._require_api_access():
                return
            # The library is a handful of small markdown files, so listing it
            # whole is cheaper than making the page ask skill-by-skill. The UI
            # filters locally; the agent's `skill` action does the scoring.
            try:
                from .skills import get_skill_manager
                manager = get_skill_manager()
                active = manager.active()
                self._json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "active": active.slug if active else "",
                        "skills": [
                            {
                                "name": skill.name,
                                "slug": skill.slug,
                                "description": skill.description,
                                "when_to_use": skill.when_to_use,
                                "tools": list(skill.tools),
                                "builtin": bool(skill.builtin),
                                "updated": skill.updated,
                            }
                            for skill in manager.list_skills()
                        ],
                    },
                )
            except Exception as exc:  # a broken skill library must not 500 the page
                self._json(
                    HTTPStatus.OK,
                    {"ok": False, "error": str(exc), "skills": [], "active": ""},
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

    def _handle_live_config(self) -> None:
        if not self._require_api_access():
            return
        from .config import load_config
        from .live import direct_tools, readiness
        from .live.prompts import build_live_voice_system_prompt, get_live_voice_tools
        cfg = load_config()
        ws_url = (
            getattr(cfg.live_voice, "ws_url", "")
            or os.environ.get("JARVIS_LIVE_WS_URL")
            or os.environ.get("JARVIS_REALTIME_URL")
            # Deliberately no built-in fallback URL. A placeholder that no one
            # is listening on is worse than an empty string: the page opened a
            # socket to it, retried forever, and looked like a live session.
            or ""
        )
        model_name = getattr(cfg.live_voice, "model", "") or "gpt-realtime"
        voice_name = getattr(cfg.live_voice, "voice_name", "") or "en-US-Ava:DragonHDLatestNeural"
        fish_agent_id = str(
            getattr(cfg.live_voice, "fish_agent_id", "")
            or os.environ.get("JARVIS_FISH_AGENT_ID")
            or os.environ.get("FISH_AGENT_ID")
            or ""
        ).strip()
        provider = readiness.resolve_provider(cfg)
        # Which voice paths can actually authenticate right now, and what each
        # one is missing. Sent with the config so the page can explain a failed
        # start without the user opening the browser console.
        voice_status = readiness.summary(readiness.describe(cfg), provider)
        self._json(
            HTTPStatus.OK,
            {
                "ok": True,
                "provider": provider,
                # `getattr(self, "server", None)`: the handler is also driven
                # directly by the tests, with no socket server behind it, and a
                # missing relay must read as "not running" rather than an
                # AttributeError in the middle of the config payload.
                "gemini": _gemini_session_block(cfg, _live_relay(self)),
                "model": model_name,
                "ws_url": ws_url,
                "voice": voice_name,
                "sample_rate": 24000,
                "system_prompt": build_live_voice_system_prompt(),
                "tools": get_live_voice_tools(),
                "fish_agent_id": fish_agent_id,
                # The voice agent's direct tools: names only. The page registers
                # a handler for each that posts to /api/tool/execute.
                "direct_tools": [tool["name"] for tool in direct_tools.declarations()],
                **voice_status,
            },
        )

    def _handle_interrupt(self) -> None:
        """Stop the active directive, or say there was nothing to stop.

        Asking an idle Jarvis to stop is a no-op, not a conflict: answering 409
        put a red error in the browser console every time the user pressed
        INTERRUPT while nothing was running.
        """
        bridge = self.server.bridge
        idle = bool(getattr(bridge, "accepting_input", False))
        ok, message = bridge.request_interrupt()
        if not ok and idle:
            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "interrupted": False,
                    "message": "nothing to interrupt",
                    "error": None,
                },
            )
            return
        self._json(
            HTTPStatus.OK if ok else HTTPStatus.CONFLICT,
            {
                "ok": ok,
                "interrupted": ok,
                "message": message,
                "error": None if ok else message,
            },
        )

    def _handle_tool_execute(self, payload: dict[str, Any]) -> None:
        """Run one Jarvis action directly, for the voice agent's tools.

        The main agent reaches actions through the perceive/think/act loop. The
        voice agent has no use for that loop on deterministic requests, so it
        names the action itself and gets the answer in one hop. Always answers
        200 with an ``ok`` flag, because a failed action is still a valid result
        the voice agent has to report out loud.
        """
        if not self._require_api_access():
            return
        from .live import direct_tools

        name = str(payload.get("tool") or payload.get("name") or "").strip()
        args = payload.get("args")
        if not name:
            self._json(
                HTTPStatus.BAD_REQUEST, {"ok": False, "error": "tool is required"}
            )
            return
        if args is not None and not isinstance(args, dict):
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "args must be an object"},
            )
            return
        if not direct_tools.is_direct(name):
            self._json(
                HTTPStatus.FORBIDDEN,
                {
                    "ok": False,
                    "error": (
                        f"'{name}' is not an available direct tool; use "
                        f"execute_task to delegate it"
                    ),
                },
            )
            return

        result = self.server.bridge.execute_tool(
            name,
            args,
            timeout=float(direct_tools.TOOL_TIMEOUT_SECONDS),
        )
        self._json(
            HTTPStatus.OK,
            {
                "ok": bool(result.get("ok")),
                "tool": name,
                "result": str(result.get("result", "")),
                "error": str(result.get("error", "")),
            },
        )

    def _handle_voice_session(self, payload: dict[str, Any]) -> None:
        """Open the browser's live voice session on the engine that is selected.

        For Gemini that means the loopback relay: the page speaks the Live API
        protocol to ``ws://127.0.0.1:<port>/live`` and never sees a credential.
        For Fish it mints the hosted agent's session token, as before.
        """
        if not self._require_api_access():
            return
        from .config import load_config
        from .live import fish_agents, readiness

        cfg = load_config()
        if readiness.resolve_provider(cfg) == "gemini":
            self._handle_gemini_session(cfg)
            return
        agent_id = str(
            payload.get("agent_id")
            or getattr(cfg.live_voice, "fish_agent_id", "")
            or os.environ.get("JARVIS_FISH_AGENT_ID")
            or os.environ.get("FISH_AGENT_ID")
            or ""
        ).strip()
        if not agent_id:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {
                    "ok": False,
                    "error": (
                        "no Fish agent configured: set live_voice.fish_agent_id "
                        "or JARVIS_FISH_AGENT_ID"
                    ),
                },
            )
            return
        try:
            api_key = fish_agents.resolve_api_key(cfg.voice)
            session = fish_agents.create_session(
                api_key,
                agent_id,
                base_url=getattr(cfg.live_voice, "fish_api_base", "") or None,
            )
        except fish_agents.FishAPIError as exc:
            # A 402 is a billing state, not a hiccup: retrying it can never
            # succeed. Remember it so readiness reports the truth on the next
            # page load and a second start does not spend another API call to
            # relearn the same answer.
            if exc.status == 402:
                readiness.remember_failure("fish", str(exc))
            # Pass the upstream status through instead of flattening everything
            # into a 502: "out of credit" and "service is unreachable" need
            # different words, and the page acts on the difference.
            self._json(
                HTTPStatus(exc.status) if exc.status in _PASSTHROUGH_STATUS
                else HTTPStatus.BAD_GATEWAY,
                {
                    "ok": False,
                    "code": exc.code,
                    "error": str(exc),
                    "hint": exc.hint,
                },
            )
            return
        except Exception as exc:
            self._json(
                HTTPStatus.BAD_GATEWAY,
                {"ok": False, "code": "upstream", "error": str(exc)},
            )
            return
        # A session that succeeded proves the account can pay, so any earlier
        # credit failure is stale and must not keep masking a working path.
        readiness.clear_failures("fish")
        self._json(
            HTTPStatus.OK,
            {"ok": True, "agent_id": agent_id, "session": session},
        )

    def _handle_gemini_session(self, cfg: Any) -> None:
        """Hand the page the loopback relay's URL, or say what is missing.

        Nothing is minted and no key is returned: the relay already holds the
        credential, and the page only needs to know where to connect.
        """
        from .live import readiness

        relay = _live_relay(self)
        if relay is None or not relay.port:
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {
                    "ok": False,
                    "provider": "gemini",
                    "error": "the live voice relay is not running",
                    "remedy": "restart Jarvis with --browser so the relay can start",
                },
            )
            return
        if not readiness.gemini_api_key(cfg):
            self._json(
                HTTPStatus.BAD_REQUEST,
                {
                    "ok": False,
                    "provider": "gemini",
                    "error": "no Gemini API key is configured",
                    "remedy": (
                        "create a free key at https://aistudio.google.com/apikey and set "
                        "JARVIS_LIVE_API_KEY in .env"
                    ),
                },
            )
            return
        block = relay.describe(cfg)
        self._json(
            HTTPStatus.OK,
            {
                "ok": True,
                "provider": "gemini",
                "ws_url": relay.url,
                "model": block["model"],
                "voice": block["voice"],
                "sample_rate_in": block["sample_rate_in"],
                "sample_rate_out": block["sample_rate_out"],
                "screen_share": block["screen_share"],
                "google_search": block["google_search"],
                "notice": block["notice"],
            },
        )

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
            "/api/live/state",
            "/api/live/execute",
            "/api/live/config",
            "/api/voice/session",
            "/api/tool/execute",
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

        if path == "/api/live/config":
            self._handle_live_config()
            return

        if path == "/api/live/state":
            if not self._require_api_access():
                return
            active = bool(payload.get("active", False))
            self.server.bridge.set_live_voice_active(active)
            self._json(HTTPStatus.OK, {"ok": True, "active": active})
            return

        if path == "/api/live/execute":
            if not self._require_api_access():
                return
            task = str(payload.get("task", "")).strip()
            if not task:
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "task is required"})
                return
            ok, message = self.server.bridge.submit(
                task,
                display_text=f"🎙️ [Voice Directive]: {task}",
            )
            self._json(
                HTTPStatus.OK if ok else HTTPStatus.CONFLICT,
                {
                    "ok": ok,
                    "task": task,
                    "message": message,
                    "state": self.server.bridge.state,
                },
            )
            return

        if path == "/api/tool/execute":
            self._handle_tool_execute(payload)
            return

        if path == "/api/voice/session":
            self._handle_voice_session(payload)
            return

        if path == "/api/interrupt":
            self._handle_interrupt()
            return

        ok, message = self.server.bridge.request_shutdown()
        self._json(
            HTTPStatus.OK if ok else HTTPStatus.CONFLICT,
            {"ok": ok, "message": message, "error": None if ok else message},
        )


#: How many frames of *audible* microphone audio count as "the user spoke".
#: Roughly three seconds of speech, and the decoded peak is only measured until
#: this many have been counted, so a long session does not decode every frame.
_AUDIBLE_FRAME_LIMIT = 12

#: Peak amplitude (of 32767) that counts as speech rather than room noise.
_AUDIBLE_PEAK = 500

#: Once the user has spoken and the model has still produced nothing for this
#: many seconds, the session is moved to the next candidate. It has to cover a
#: slow cold start and a first turn: transcripts appear at a turn boundary, so a
#: user talking without pausing produces nothing for as long as they keep going.
#: ``gemini_live._DEAF_STRIKES`` is what stops one slow session from condemning
#: a model for the life of the process.
_SILENCE_WATCHDOG_SECONDS = 8.0

#: How long the microphone has to be quiet before silence from the model means
#: anything. Nothing is concluded while the user is still talking: the model
#: answers at a turn boundary, so a continuous stream is silent from a perfectly
#: working model.
_QUIET_BEFORE_VERDICT_SECONDS = 4.0


def _silent_session_notice(audible_frames: int, model_messages: int) -> str:
    """What to tell the user about a session that heard them and said nothing.

    The page shows LISTENING for the whole session, so without this a dead
    session is indistinguishable from a working one - which is exactly what a
    spent Live allowance produces on the free tier.
    """
    if audible_frames >= _AUDIBLE_FRAME_LIMIT and not model_messages:
        return (
            "Your voice reached the model and it produced no answer. That is usually a "
            "spent Live quota on the API key, which looks like a healthy session from here."
        )
    return ""


def _audible(raw: str) -> int:
    """1 if this page message carries speech, 0 otherwise.

    Silence detection has to happen here rather than in the page: `isAiSpeaking`
    already suppresses the microphone while the model talks, so frames arriving
    with no sound in them mean the user simply has not spoken yet - which is not
    a fault and must not be reported as one.
    """
    try:
        message = json.loads(raw)
        inner = (message.get("realtimeInput") or {}).get("audio") or {}
        data = inner.get("data")
        if not data:
            return 0
        import audioop

        pcm = base64.b64decode(data)
        return 1 if audioop.max(pcm, 2) >= _AUDIBLE_PEAK else 0
    except Exception:
        return 0


def _live_relay(handler: Any) -> "LiveSocketRelay | None":
    """The relay behind a request handler, or ``None`` when there is none.

    ``BrowserRequestHandler`` is driven in tests through ``object.__new__`` with
    no socket server, so ``self.server`` - normally set by the stdlib - may not
    exist at all. Reading it defensively keeps one missing attribute from
    turning a config response into a 500.
    """
    server = getattr(handler, "server", None)
    return getattr(server, "live_relay", None)


def _gemini_session_block(cfg: Any, relay: "LiveSocketRelay | None" = None) -> dict[str, Any]:
    """What the page needs to run the Gemini Live session - or why it cannot.

    ``relay.describe`` covers the live case. Without a relay (a server built
    directly, or a machine where the relay could not bind) the block still names
    the model, voice and sample rates, so the page reports the real reason
    instead of claiming live voice was never configured.
    """
    from .live import gemini_live, readiness

    live = getattr(cfg, "live_voice", None)
    if relay is not None and relay.port:
        block = relay.describe(cfg)
    else:
        block = {
            "ready": False,
            "ws_url": "",
            "model": getattr(live, "model", "") or gemini_live.DEFAULT_MODEL,
            "voice": getattr(live, "voice_name", "") or gemini_live.DEFAULT_VOICE,
            "media_resolution": gemini_live.media_resolution(
                getattr(live, "media_resolution", "medium")
            ),
            "sample_rate_in": gemini_live.PCM_IN_RATE,
            "sample_rate_out": gemini_live.PCM_OUT_RATE,
            "google_search": {
                "mode": str(getattr(live, "google_search", "auto") or "auto"),
                "active": False,
                "notice": "",
            },
            "screen_share": {
                "enabled": bool(getattr(live, "screen_share", True)),
                "interval": float(getattr(live, "screen_share_interval", 1.0) or 1.0),
                "max_dim": int(getattr(live, "screen_share_max_dim", 1024) or 1024),
                "quality": int(getattr(live, "screen_share_quality", 60) or 60),
            },
            "sessions": 0,
            "error": "the live voice relay is not running",
            "notice": "",
        }

    # The page answers three kinds of tool call: the delegation tools it already
    # handled for Fish, screen sharing (decided in the page, because the frames
    # come from getDisplayMedia), and everything else, which is a Jarvis action
    # posted to /api/tool/execute like any other direct tool.
    block["control_tools"] = ["execute_task", "cancel_task", "get_task_status"]
    block["screen_share_tools"] = ["share_screen", "stop_screen_share"]
    if not readiness.gemini_api_key(cfg):
        block["ready"] = False
        block["error"] = block.get("error") or "no Gemini API key is configured"
    if not block.get("ws_url"):
        block["ready"] = False
    return block


def _port_serves_our_token(host: str, port: int, token: str, timeout: float = 2.5) -> bool:
    """True only when this loopback port answers with *our* session token.

    Used once at startup to refuse a port another Jarvis instance already owns.
    A 401 means someone else is answering.
    """
    import urllib.error
    import urllib.request

    url = f"http://{host}:{port}/api/state?token={quote(token)}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


def run_browser(
    child_args: list[str] | None = None,
    initial_task: str | None = None,
    remote_agent: bool = False,
    live_voice: bool = False,
) -> int:
    """Start the loopback UI and its console or remote-agent worker."""
    if not all((STATIC_DIR / name).is_file()
               for name in ("index.html", "styles.css", "app.js")):
        print("Jarvis browser UI assets are missing.", file=sys.stderr)
        return 1

    token = secrets.token_urlsafe(32)
    bridge = TerminalBridge(
        child_args=child_args,
        initial_task=initial_task,
        token=token,
        interface_mode="remote-agent" if remote_agent else "console",
        live_voice=live_voice,
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

    port = int(server.server_address[1])
    if not _port_serves_our_token(HOST, port, token):
        # Windows lets a second process bind a port that is already in use
        # (allow_reuse_address), so a successful bind does NOT mean the page
        # will reach this server: an older Jarvis instance keeps answering with
        # its own token, and the page sits at "RELINKING" forever with no clue
        # why. Ask the port to authorise our own token before spawning a runtime
        # that nothing could talk to.
        server.shutdown()
        server.server_close()
        print(
            f"  Port {port} is already served by another Jarvis instance.\n"
            f"  That instance would answer this page with its own token, so this\n"
            f"  one has stopped instead of fighting it over the port.\n"
            f"  Close the other Jarvis, or start this one with a different\n"
            f"  JARVIS_BROWSER_PORT.",
            file=sys.stderr,
        )
        return 1

    # Live voice's Gemini session runs through this relay, so the API key stays
    # on the machine while the audio stays in the page. A relay that cannot bind
    # is not fatal: the page reports exactly that instead of a dead caption.
    relay = LiveSocketRelay(bridge, origin=server.origin)
    server.live_relay = relay
    if not relay.start():
        log.warn("Live voice relay could not start; browser live voice will report why.")

    try:
        bridge.start()
    except Exception as exc:
        relay.stop()
        server.shutdown()
        server.server_close()
        print(f"Could not start Jarvis terminal runtime: {exc}", file=sys.stderr)
        return 1

    base_url = f"http://{HOST}:{port}/"
    frag = f"#live=true&token={quote(token)}" if live_voice else f"#token={quote(token)}"
    browser_url = f"{base_url}{frag}"
    print()
    label = "remote agent dashboard" if remote_agent else "browser interface"
    print(f"  JARVIS {label} online")
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
        relay.stop()
        server.shutdown()
        server.server_close()
        bridge.stop()
        server_thread.join(timeout=1.0)

    return int(bridge.process.returncode or 0) if bridge.process else 0
