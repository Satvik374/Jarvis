"""Model Context Protocol (MCP) support for Jarvis.

Lets Jarvis connect to MCP servers - the same connectors Claude Desktop, Cursor
and friends use - and call their tools as if they were built-in actions. Two
ways in:

  * the user runs  ``/mcp add <name> <command...>``  in the console, or
  * Jarvis calls the ``mcp`` action himself (op = add/remove/enable/disable/...).

Enabled servers' tools are listed to the model each task (:func:`tools_note`);
the model invokes one with the ``mcp_call`` action.

Transport: newline-delimited JSON-RPC 2.0 over the server subprocess's
stdin/stdout - the MCP *stdio* transport - implemented here with the stdlib
only, so there is no `mcp` package dependency to install.

Server config lives in ``mcp_servers.json`` at the project root, in the same
shape Claude Desktop uses::

    {"mcpServers": {"filesystem": {"command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:/Users"],
        "env": {}, "disabled": false}}}
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path

from .utils import logging as log

PROTOCOL_VERSION = "2024-11-05"
_RESULT_CAP = 6000            # chars of a tool result fed back to the model


class MCPError(RuntimeError):
    """Any failure talking to an MCP server."""


# --------------------------------------------------------------------------- #
# stdio JSON-RPC client
# --------------------------------------------------------------------------- #
class MCPClient:
    """One connected MCP server subprocess. Not thread-safe for concurrent
    requests on purpose - a lock serialises them (one call in flight)."""

    def __init__(self, command: str, args: list[str] | None = None,
                 env: dict | None = None, cwd: str | None = None):
        self.command = command
        self.args = list(args or [])
        self.env = dict(env or {})
        self.cwd = cwd
        self._proc: subprocess.Popen | None = None
        self._q: queue.Queue = queue.Queue()
        self._stderr: list[str] = []
        self._lock = threading.Lock()
        self._id = 0
        self.server_info: dict = {}

    # -- lifecycle ----------------------------------------------------- #
    def connect(self, timeout: float = 60.0) -> "MCPClient":
        argv = _popen_argv(self.command, self.args)
        full_env = {**os.environ, **{k: str(v) for k, v in self.env.items()}}
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            self._proc = subprocess.Popen(
                argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                errors="replace", bufsize=1, env=full_env,
                cwd=self.cwd or None, creationflags=flags)
        except FileNotFoundError:
            raise MCPError(f"command not found: {self.command!r} (is it installed "
                           "and on PATH?)")
        except Exception as exc:
            raise MCPError(f"could not launch: {exc}")
        threading.Thread(target=self._read_stdout, daemon=True,
                         name="mcp-reader").start()
        threading.Thread(target=self._read_stderr, daemon=True,
                         name="mcp-stderr").start()

        result = self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "jarvis", "version": "1.0"},
        }, timeout)
        self.server_info = result.get("serverInfo", {}) if isinstance(result, dict) else {}
        self._notify("notifications/initialized", {})
        return self

    def close(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except Exception:
                self._proc.kill()
        except Exception:
            pass
        for stream in (self._proc.stdin, self._proc.stdout, self._proc.stderr):
            try:
                stream.close()
            except Exception:
                pass
        self._proc = None

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # -- MCP methods --------------------------------------------------- #
    def list_tools(self, timeout: float = 30.0) -> list[dict]:
        result = self._request("tools/list", {}, timeout)
        tools = result.get("tools", []) if isinstance(result, dict) else []
        # ponytail: no cursor pagination - one page covers every server we've
        # met. Add nextCursor looping if a server ever truncates its tool list.
        return [t for t in tools if isinstance(t, dict) and t.get("name")]

    def call_tool(self, name: str, arguments: dict | None = None,
                  timeout: float = 120.0) -> tuple[bool, str]:
        """Returns ``(ok, text)`` - ok is False when the server flags isError."""
        result = self._request("tools/call",
                               {"name": name, "arguments": arguments or {}},
                               timeout)
        if not isinstance(result, dict):
            return True, str(result)[:_RESULT_CAP]
        text = _content_text(result.get("content", []))
        return (not result.get("isError", False)), (text[:_RESULT_CAP] or "(no output)")

    # -- transport ----------------------------------------------------- #
    def _read_stdout(self) -> None:
        try:
            for line in self._proc.stdout:        # type: ignore[union-attr]
                line = line.strip()
                if not line:
                    continue
                try:
                    self._q.put(json.loads(line))
                except Exception:
                    continue                       # non-JSON log line: ignore
        except Exception:
            pass
        self._q.put(None)                          # EOF sentinel

    def _read_stderr(self) -> None:
        try:
            for line in self._proc.stderr:        # type: ignore[union-attr]
                self._stderr.append(line.rstrip())
                del self._stderr[:-20]             # keep only the last 20 lines
        except Exception:
            pass

    def _send(self, obj: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise MCPError("server is not running")
        try:
            self._proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()
        except Exception as exc:
            raise MCPError(f"failed to send to server: {exc}")

    def _notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params: dict, timeout: float) -> dict:
        with self._lock:
            self._id += 1
            rid = self._id
            self._send({"jsonrpc": "2.0", "id": rid, "method": method,
                        "params": params})
            deadline = time.time() + timeout
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise MCPError(self._timeout_msg(method))
                try:
                    msg = self._q.get(timeout=remaining)
                except queue.Empty:
                    raise MCPError(self._timeout_msg(method))
                if msg is None:
                    raise MCPError("server closed the connection"
                                   + self._stderr_hint())
                if msg.get("id") == rid:
                    if "error" in msg:
                        raise MCPError(str(msg["error"]))
                    return msg.get("result", {})
                # A server->client request (sampling/roots) we don't support:
                # decline it so the server doesn't block waiting on us.
                if "method" in msg and "id" in msg:
                    self._send({"jsonrpc": "2.0", "id": msg["id"],
                                "error": {"code": -32601,
                                          "message": "not supported"}})
                # otherwise a notification -> ignore and keep waiting.

    def _timeout_msg(self, method: str) -> str:
        return f"timed out waiting for {method}" + self._stderr_hint()

    def _stderr_hint(self) -> str:
        tail = " ".join(self._stderr[-3:]).strip()
        return f" (server said: {tail[:200]})" if tail else ""


def _content_text(content) -> str:
    """Flatten an MCP tool result's content blocks into plain text."""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif block.get("type") == "resource":
            res = block.get("resource", {})
            parts.append(str(res.get("text") or res.get("uri") or ""))
        else:
            parts.append(f"[{block.get('type', 'content')}]")
    return "\n".join(p for p in parts if p)


def _popen_argv(command: str, args: list[str]) -> list[str]:
    """Resolve ``command`` on PATH and build a Windows-safe argv. ``npx``/``npm``
    resolve to a ``.cmd`` shim which CreateProcess can't exec directly, so those
    go through ``cmd /c``."""
    resolved = shutil.which(command) or command
    if os.name == "nt" and resolved.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", resolved, *args]
    return [resolved, *args]


# --------------------------------------------------------------------------- #
# server manager (config + live connections + tool cache)
# --------------------------------------------------------------------------- #
class Manager:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._servers: dict[str, dict] = self._load()
        self._clients: dict[str, MCPClient] = {}
        self._tools: dict[str, list[dict]] = {}     # cached, params precomputed
        self._errors: dict[str, str] = {}
        self._lock = threading.RLock()

    # -- config -------------------------------------------------------- #
    def _load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warn(f"could not read {self.path.name}: {exc}")
            return {}
        servers = data.get("mcpServers", data) if isinstance(data, dict) else {}
        return {str(k): dict(v) for k, v in servers.items()
                if isinstance(v, dict)}

    def _save(self) -> None:
        try:
            self.path.write_text(
                json.dumps({"mcpServers": self._servers}, indent=2),
                encoding="utf-8")
        except Exception as exc:
            log.warn(f"could not save {self.path.name}: {exc}")

    def servers(self) -> dict[str, dict]:
        return dict(self._servers)

    def enabled(self) -> list[str]:
        return [n for n, c in self._servers.items() if not c.get("disabled")]

    def add(self, name: str, command: str, args: list | None = None,
            env: dict | None = None, enable: bool = True) -> None:
        with self._lock:
            self._servers[name] = {"command": command,
                                   "args": list(args or []),
                                   "env": dict(env or {}),
                                   "disabled": not enable}
            self._save()
            self._drop_client(name)

    def remove(self, name: str) -> bool:
        with self._lock:
            if name not in self._servers:
                return False
            del self._servers[name]
            self._save()
            self._drop_client(name)
            return True

    def set_enabled(self, name: str, enabled: bool) -> bool:
        with self._lock:
            if name not in self._servers:
                return False
            self._servers[name]["disabled"] = not enabled
            self._save()
            if not enabled:
                self._drop_client(name)
            return True

    # -- connections --------------------------------------------------- #
    def _drop_client(self, name: str) -> None:
        client = self._clients.pop(name, None)
        self._tools.pop(name, None)
        self._errors.pop(name, None)
        if client is not None:
            client.close()

    def _ensure(self, name: str) -> MCPClient:
        """Return a connected client for ``name``, connecting on first use."""
        with self._lock:
            client = self._clients.get(name)
            if client is not None and client.alive():
                return client
            cfg = self._servers[name]
            client = MCPClient(cfg.get("command", ""), cfg.get("args"),
                               cfg.get("env"), cfg.get("cwd"))
            client.connect()
            self._clients[name] = client
            self._tools[name] = [_summarise_tool(t) for t in client.list_tools()]
            self._errors.pop(name, None)
            return client

    def connect(self, name: str) -> tuple[bool, str]:
        if name not in self._servers:
            return False, f"no MCP server named '{name}'"
        if self._servers[name].get("disabled"):
            return False, f"'{name}' is disabled"
        try:
            self._ensure(name)
        except Exception as exc:
            self._errors[name] = str(exc)
            return False, str(exc)
        return True, f"connected ({len(self._tools.get(name, []))} tools)"

    def connect_all(self, background: bool = True) -> None:
        """Warm up every enabled server so its tools are ready before the first
        task. Failures are recorded, never raised."""
        names = self.enabled()
        if not names:
            return

        def _work():
            for n in names:
                ok, msg = self.connect(n)
                if ok:
                    log.info(f"MCP: {n} {msg}")
                else:
                    log.warn(f"MCP: {n} failed - {msg}")

        if background:
            threading.Thread(target=_work, daemon=True, name="mcp-connect").start()
        else:
            _work()

    def close_all(self) -> None:
        with self._lock:
            for client in self._clients.values():
                client.close()
            self._clients.clear()

    # -- use ----------------------------------------------------------- #
    def cached_tools(self, name: str) -> list[dict] | None:
        """Tools for a server if already fetched, else None (not yet connected)."""
        return self._tools.get(name)

    def error(self, name: str) -> str | None:
        return self._errors.get(name)

    def list_tools(self, name: str) -> tuple[bool, list[dict] | str]:
        """Force a connect if needed, then return the tool list (or an error)."""
        ok, msg = self.connect(name)
        if not ok:
            return False, msg
        return True, self._tools.get(name, [])

    def call(self, server: str, tool: str, arguments: dict) -> tuple[bool, str]:
        if server not in self._servers:
            avail = ", ".join(self._servers) or "(none configured)"
            return False, f"no MCP server named '{server}'. Configured: {avail}"
        if self._servers[server].get("disabled"):
            return False, f"MCP server '{server}' is disabled - enable it first"
        try:
            client = self._ensure(server)
        except Exception as exc:
            self._errors[server] = str(exc)
            return False, f"could not start MCP server '{server}': {exc}"
        try:
            return client.call_tool(tool, arguments)
        except MCPError as exc:
            return False, f"MCP call '{server}.{tool}' failed: {exc}"


def _summarise_tool(tool: dict) -> dict:
    """Keep only what the model needs, and precompute the param-name list."""
    schema = tool.get("inputSchema") or {}
    props = schema.get("properties", {}) if isinstance(schema, dict) else {}
    required = set(schema.get("required", []) if isinstance(schema, dict) else [])
    params = [p if p in required else f"{p}?" for p in props]
    return {"name": tool["name"],
            "description": (tool.get("description") or "").strip(),
            "params": params}


# --------------------------------------------------------------------------- #
# singleton (reached by the console, the registry action and the prompt)
# --------------------------------------------------------------------------- #
_MANAGER: Manager | None = None


def get_manager() -> Manager:
    global _MANAGER
    if _MANAGER is None:
        root = Path(__file__).resolve().parent.parent
        _MANAGER = Manager(root / "mcp_servers.json")
    return _MANAGER


def tools_note() -> str:
    """The MCP TOOLS section injected into the main agent's system prompt.
    Empty when no servers are configured, so the prompt never bloats."""
    mgr = get_manager()
    names = mgr.enabled()
    if not names:
        return ""
    lines: list[str] = []
    for name in names:
        tools = mgr.cached_tools(name)
        if tools is None:
            err = mgr.error(name)
            lines.append(f"  [{name}] {'unavailable: ' + err if err else 'connecting…'}")
            continue
        if not tools:
            lines.append(f"  [{name}] (no tools)")
        for t in tools:
            sig = f"{name}.{t['name']}({', '.join(t['params'])})"
            desc = t["description"].splitlines()[0][:90] if t["description"] else ""
            lines.append(f"  {sig:<44} {desc}")
    return ("\n\n=== MCP TOOLS (call with mcp_call: {\"action\":\"mcp_call\",\"args\":"
            "{\"server\":\"<name>\",\"tool\":\"<tool>\",\"arguments\":{...}}}) ===\n"
            + "\n".join(lines) +
            "\n=================================================================")


def manage(args: dict) -> str:
    """Shared handler for the ``mcp`` action (Jarvis configuring his own
    servers). ``args`` = {op, name?, command?, args?, env?}. Returns a
    human-readable status string."""
    mgr = get_manager()
    op = str(args.get("op", "")).strip().lower()
    name = str(args.get("name", "")).strip()

    if op in ("list", ""):
        servers = mgr.servers()
        if not servers:
            return ("No MCP servers configured. Add one with op='add', e.g. "
                    "command='npx', args=['-y','@modelcontextprotocol/server-filesystem','C:/Users'].")
        rows = []
        for n, c in servers.items():
            state = "disabled" if c.get("disabled") else "enabled"
            cmd = " ".join([c.get("command", "")] + list(c.get("args", [])))
            n_tools = mgr.cached_tools(n)
            extra = f", {len(n_tools)} tools" if n_tools is not None else ""
            rows.append(f"- {n} [{state}{extra}]: {cmd}")
        return "MCP servers:\n" + "\n".join(rows)

    if op == "add":
        command = str(args.get("command", "")).strip()
        if not name or not command:
            return "mcp add needs 'name' and 'command'."
        a = args.get("args") or []
        a = [str(x) for x in a] if isinstance(a, list) else []
        env = args.get("env") if isinstance(args.get("env"), dict) else {}
        mgr.add(name, command, a, env)
        ok, msg = mgr.connect(name)
        return (f"Added MCP server '{name}'. " +
                (f"Connected - {msg}." if ok else f"But it did not start: {msg}"))

    if op in ("remove", "delete", "rm"):
        return (f"Removed MCP server '{name}'." if mgr.remove(name)
                else f"No MCP server named '{name}'.")

    if op in ("enable", "disable"):
        want = op == "enable"
        if not mgr.set_enabled(name, want):
            return f"No MCP server named '{name}'."
        if want:
            ok, msg = mgr.connect(name)
            return f"Enabled '{name}'. " + (msg if ok else f"(not reachable: {msg})")
        return f"Disabled '{name}'."

    if op == "tools":
        if not name:
            return "mcp tools needs a 'name' (which server)."
        ok, tools = mgr.list_tools(name)
        if not ok:
            return f"'{name}': {tools}"
        if not tools:
            return f"'{name}' exposes no tools."
        rows = [f"- {t['name']}({', '.join(t['params'])}) - {t['description'][:120]}"
                for t in tools]
        return f"Tools on '{name}':\n" + "\n".join(rows)

    return ("unknown mcp op - use list, add, remove, enable, disable, or tools.")
