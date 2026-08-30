"""Interactive Stateful Shell and REPL Session Engine for Jarvis.

Enables persistent shell sessions (PowerShell, CMD, Bash, Python) where environment
variables, directory changes, virtual environments, and process state persist
across multiple turns without losing state.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .files import _expand, _within
from .system import _threatens_self


@dataclass
class SessionInfo:
    name: str
    shell_type: str
    pid: int
    cwd: str
    created_at: float
    last_used: float
    commands_run: int = 0


class InteractiveSession:
    """A long-running, stateful interactive subprocess session with sentinel-based I/O."""

    def __init__(self, name: str, shell_type: str = "powershell", cwd: Optional[str] = None):
        self.name = name
        self.shell_type = shell_type.lower()
        self.cwd = cwd or str(Path.home())
        self.created_at = time.time()
        self.last_used = time.time()
        self.commands_run = 0
        self._lock = threading.Lock()

        # Build startup command
        if self.shell_type in ("powershell", "pwsh"):
            cmd = ["powershell", "-NoProfile", "-NoLogo", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", "-"]
        elif self.shell_type == "cmd":
            cmd = ["cmd", "/Q", "/K"]
        elif self.shell_type in ("bash", "sh"):
            cmd = ["bash", "-s"] if os.name != "nt" else ["cmd", "/Q", "/K"]
        elif self.shell_type in ("python", "py"):
            cmd = [sys.executable, "-u", "-q", "-i"]
        else:
            cmd = ["powershell", "-NoProfile", "-NoLogo", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", "-"]

        sub_env = dict(os.environ)
        sub_env["PYTHONIOENCODING"] = "utf-8"

        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=self.cwd,
            env=sub_env,
        )

        self._out_queue: queue.Queue = queue.Queue()
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            daemon=True,
            name=f"jarvis-session-{name}",
        )
        self._reader_thread.start()

    def _reader_loop(self) -> None:
        """Continuously read lines from the process stdout/stderr."""
        try:
            if self.proc.stdout:
                for line in iter(self.proc.stdout.readline, ""):
                    self._out_queue.put(line)
        except Exception:
            pass
        finally:
            self._out_queue.put(None)  # Process terminated marker

    def is_alive(self) -> bool:
        return self.proc.poll() is None

    def execute(self, command: str, timeout: float = 30.0) -> str:
        """Run a command inside the persistent session and return output up to sentinel."""
        with self._lock:
            if not self.is_alive():
                return f"session '{self.name}' has terminated (exit code {self.proc.returncode})"

            self.last_used = time.time()
            self.commands_run += 1
            cmd_clean = command.strip()

            token = f"__JARVIS_DONE_{uuid.uuid4().hex[:8]}__"

            # Flush any stale output in queue
            while not self._out_queue.empty():
                try:
                    self._out_queue.get_nowait()
                except queue.Empty:
                    break

            # Send command + sentinel
            if self.shell_type in ("powershell", "pwsh"):
                full_input = f"{cmd_clean}\nWrite-Output '{token}'\n"
            elif self.shell_type == "cmd":
                full_input = f"{cmd_clean}\n@echo {token}\n"
            elif self.shell_type in ("python", "py"):
                full_input = f"{cmd_clean}\nprint('{token}')\n"
            else:
                full_input = f"{cmd_clean}\necho '{token}'\n"

            try:
                if self.proc.stdin:
                    self.proc.stdin.write(full_input)
                    self.proc.stdin.flush()
            except Exception as exc:
                return f"failed to write to session stdin: {exc}"

            # Read until sentinel is seen or timeout expires
            collected: List[str] = []
            deadline = time.time() + max(1.0, timeout)

            while time.time() < deadline:
                try:
                    line = self._out_queue.get(timeout=0.1)
                except queue.Empty:
                    if not self.is_alive():
                        collected.append(f"\n[session exited with code {self.proc.returncode}]")
                        break
                    continue

                if line is None:
                    collected.append(f"\n[session exited with code {self.proc.returncode}]")
                    break

                if token in line:
                    # Sentinel found: command completed
                    break

                collected.append(line)

            output = "".join(collected).strip()
            if not output and self.is_alive():
                return "(command executed successfully with no output)"
            return output[:8000]

    def close(self) -> None:
        """Gracefully terminate the session subprocess."""
        if not self.is_alive():
            return
        try:
            if self.proc.stdin:
                try:
                    self.proc.stdin.write("exit\n")
                    self.proc.stdin.flush()
                except Exception:
                    pass
            self.proc.terminate()
            self.proc.wait(timeout=2.0)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


class SessionManager:
    """Coordinates and manages multiple persistent interactive sessions."""

    def __init__(self):
        self._sessions: Dict[str, InteractiveSession] = {}
        self._lock = threading.Lock()

    def start(self, name: str = "default", shell_type: str = "powershell", cwd: Optional[str] = None) -> str:
        s_name = (name or "default").strip().lower()
        with self._lock:
            if s_name in self._sessions and self._sessions[s_name].is_alive():
                return f"session '{s_name}' is already running ({self._sessions[s_name].shell_type}, PID {self._sessions[s_name].proc.pid})"

            # Clean up old dead instance if present
            if s_name in self._sessions:
                self._sessions[s_name].close()

            session = InteractiveSession(name=s_name, shell_type=shell_type, cwd=cwd)
            self._sessions[s_name] = session
            return f"started persistent session '{s_name}' ({shell_type}, PID {session.proc.pid})"

    def execute(self, command: str, name: str = "default", timeout: float = 30.0, blocked: tuple[str, ...] = ()) -> str:
        s_name = (name or "default").strip().lower()
        low = command.lower()
        for pat in blocked:
            if pat.lower() in low:
                return f"refused: command matches blocked pattern '{pat.strip()}'"
        if _threatens_self(command):
            return "refused: that command could kill Jarvis or its host process."

        with self._lock:
            session = self._sessions.get(s_name)
            if session is None or not session.is_alive():
                # Auto-start default session if not started
                session = InteractiveSession(name=s_name, cwd=str(Path.home()))
                self._sessions[s_name] = session

        return session.execute(command, timeout=timeout)

    def list_sessions(self) -> str:
        with self._lock:
            active = []
            for name, s in list(self._sessions.items()):
                if s.is_alive():
                    uptime = int(time.time() - s.created_at)
                    active.append(f"  • [{name}] {s.shell_type} (PID {s.proc.pid}) - uptime {uptime}s, {s.commands_run} commands run")
                else:
                    self._sessions.pop(name, None)

            if not active:
                return "no active interactive sessions"
            return f"Active interactive sessions ({len(active)}):\n" + "\n".join(active)

    def close(self, name: str = "") -> str:
        s_name = (name or "").strip().lower()
        with self._lock:
            if not s_name:
                # Close all
                count = len(self._sessions)
                for s in self._sessions.values():
                    s.close()
                self._sessions.clear()
                return f"closed all {count} interactive sessions"

            if s_name not in self._sessions:
                return f"no session found named '{s_name}'"

            s = self._sessions.pop(s_name)
            s.close()
            return f"closed session '{s_name}'"


# Global singleton
_GLOBAL_SESSIONS = SessionManager()


def get_session_manager() -> SessionManager:
    return _GLOBAL_SESSIONS


def session_exec(
    op: str = "exec",
    command: str = "",
    name: str = "default",
    shell_type: str = "powershell",
    timeout: int = 30,
    cwd: Optional[str] = None,
    blocked: tuple[str, ...] = (),
    allow: tuple[str, ...] = (),
) -> str:
    """Execute persistent stateful interactive shell/REPL operations.

    Operations:
      - 'exec': Run command/expression in the named persistent session.
      - 'start': Launch a new persistent session.
      - 'list': List all running interactive sessions.
      - 'close': Terminate a named session (or all sessions if name is empty).
    """
    mgr = get_session_manager()
    op_clean = (op or "exec").strip().lower()

    if op_clean in ("exec", "run", "eval"):
        if not command.strip():
            return "session_exec 'exec' needs a command to run"
        return mgr.execute(command, name=name, timeout=float(timeout or 30), blocked=blocked)

    elif op_clean in ("start", "new", "open"):
        return mgr.start(name=name, shell_type=shell_type, cwd=cwd)

    elif op_clean in ("list", "status", "ps"):
        return mgr.list_sessions()

    elif op_clean in ("close", "stop", "kill", "exit"):
        return mgr.close(name=name)

    return f"unknown session_exec op '{op}' - supported: exec, start, list, close"
