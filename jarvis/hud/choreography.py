"""Per-action choreography: what a tool *looks like* while it runs.

A tool call is a black box to the person watching. Between the model deciding to
open an app and the app appearing, the HUD says "acting" and the browser stage
sits still - true, but indistinguishable from a hang, and a task that spends four
turns finding an app looks slow even when every single call is fast.

This module gives each action a short script of **beats**: the visible steps the
tool is actually going through ("scanning the Start Menu index for 'capcut'" ->
"launching 'capcut'" -> "waiting for its window" -> "window up"). Beats are
narrated on a worker thread so they:

  * never delay the tool - ``begin`` and ``finish`` only enqueue a command, and
    the handler's own latency is measured against a queue put;
  * never lie - a fast tool's later beats are simply cut off by ``finish``, and
    the closing beat always carries the tool's *real* result message;
  * never run without an audience - no HUD, no browser bridge and no terminal
    means ``begin`` hands back a shared no-op, so tests and headless runs pay a
    dictionary lookup and nothing else.

The scripts describe real work, which is why they earn their place: they are the
honest shape of what the tool does, and where a tool is slow enough for a person
to notice, they are the difference between "Jarvis is thinking" and "Jarvis is
launching CapCut".
"""

from __future__ import annotations

import queue
import re
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

#: Seconds a beat is held before the next one, before ``weight`` and the user's
#: speed preference are applied. Chosen so a three-beat script spans roughly a
#: second: long enough to read, short enough that a tool taking 2 s does not lap
#: its own script and sit silently on the last line.
BEAT_SECONDS = 0.45

#: Floor between two emissions. A beat that arrived faster than this would be
#: unreadable anyway (and the browser would see a flood for no visual gain).
MIN_BEAT_SECONDS = 0.12

#: Out of script but still running: how often the heartbeat line refreshes.
HEARTBEAT_SECONDS = 1.0

#: Glyphs for that heartbeat, so a long tool looks alive.
_SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

#: Hard cap on what a beat label may print, so a 5000-character command cannot
#: blow up the HUD capsule.
_LABEL_CHARS = 54
_DETAIL_CHARS = 96

_TEMPLATE = re.compile(r"\{(\w+)\}")

#: What a missing argument renders as. A beat must never raise - these labels
#: are built from whatever the model sent, including nothing at all.
#: Other keys the same concept arrives under. ``open_app`` is declared with
#: ``name``, but a few prompts send ``app``; the narration should read the value
#: that is there rather than announce a missing argument.
_ALIASES = {
    "name": ("app", "application"),
    "path": ("file", "filename", "source"),
    "src": ("source", "path"),
    "dst": ("destination", "target"),
    "query": ("q", "text", "search"),
    "text": ("content", "body", "message"),
    "url": ("link", "address"),
    "command": ("cmd",),
    "title": ("window",),
    "target": ("title", "url"),
}

_DEFAULTS = {
    "element": "the element",
    "coord": "the saved target",
    "target": "the target",
    "name": "the app",
    "path": "the file",
    "title": "the window",
    "url": "the link",
    "query": "the question",
    "command": "the command",
    "code": "the snippet",
    "keys": "the key",
    "text": "the text",
    "x": "?", "y": "?", "x1": "?", "y1": "?", "x2": "?", "y2": "?",
    "dy": "?", "dx": "?", "seconds": "?", "camera": "0", "port": "?",
    "where": "the target", "n": "0",
    "op": "the operation", "action": "the operation", "service": "the service",
    "device": "the device", "server": "the server", "tool": "the tool",
    "summary": "the result", "fact": "the memory", "source": "the file",
    "src": "the file", "dst": "the destination", "root": "the folder",
    "pattern": "the pattern", "expr": "the expression", "layout": "the layout",
    "direction": "the direction", "strategy": "the strategy",
    "schedule": "the schedule", "question": "the question", "theme": "the theme",
    "description": "the task", "sql": "the query", "mode": "the mode",
}


@dataclass(frozen=True)
class Beat:
    """One visible step of a tool's script.

    ``state`` is a HUD/browser state name (``working``, ``acting``, ``perceiving``,
    ``success``...); ``weight`` is this beat's share of the script's time; and
    ``pulse`` is how hard the browser's stage should flash for it.
    """

    glyph: str
    label: str
    state: str = "working"
    weight: float = 1.0
    pulse: float = 1.0


def _b(glyph: str, label: str, state: str = "working", weight: float = 1.0,
       pulse: float = 1.0) -> Beat:
    return Beat(glyph, label, state, weight, pulse)


# --------------------------------------------------------------------------- #
# The scripts
# --------------------------------------------------------------------------- #

#: action name -> its script. Every action in ``jarvis.tools.schema`` resolves to
#: a script: the tables below cover each family, and :func:`beats_for` falls back
#: to a generic one for anything unlisted, so a newly declared action animates the
#: day it is added instead of silently doing nothing.
SCRIPTS: dict[str, tuple[Beat, ...]] = {
    # -- pointer --------------------------------------------------------- #
    "click": (_b("🎯", "aiming at {where}", "perceiving", 0.8),
              _b("⚡", "clicking", "acting", 0.6, 1.3)),
    "double_click": (_b("🎯", "aiming at {where}", "perceiving", 0.8),
                     _b("⚡", "double-clicking", "acting", 0.6, 1.3)),
    "triple_click": (_b("🎯", "aiming at {where}", "perceiving", 0.8),
                     _b("⚡", "triple-clicking", "acting", 0.6, 1.3)),
    "right_click": (_b("🎯", "aiming at {where}", "perceiving", 0.8),
                    _b("📋", "opening the context menu", "acting", 0.6)),
    "move": (_b("➤", "sliding the cursor to {x},{y}", "acting", 0.7),),
    "drag": (_b("✋", "grabbing the source point", "acting", 0.8),
             _b("↔", "dragging it to the destination", "acting", 0.8)),
    "scroll": (_b("↕", "scrolling {dy} clicks", "acting", 0.7),),
    "mouse_control": (_b("✋", "camera hand control: {enabled}", "acting", 0.9),),

    # -- keyboard -------------------------------------------------------- #
    "type": (_b("⌨", "typing {text}", "acting", 0.8),),
    "press": (_b("⌨", "pressing {keys}", "acting", 0.7),),
    "key_sequence": (_b("⌨", "pressing {n} keys in sequence", "acting", 0.9),),

    # -- apps / os ------------------------------------------------------- #
    # The one the whole feature was asked for: the index lookup, the launch, and
    # the window it produced are three separate things the user can watch happen.
    "open_app": (_b("🔍", "scanning the Start Menu index for '{name}'",
                    "perceiving", 0.7),
                 _b("⚡", "launching '{name}'", "acting", 0.8, 1.4),
                 _b("⏳", "waiting for its window", "acting", 0.9)),
    "open_url": (_b("🌐", "opening {url}", "acting", 0.9),),
    "read_url": (_b("📡", "fetching {url}", "perceiving", 0.9),
                 _b("📖", "reading the page", "working", 1.2)),
    "browser_action": (_b("🤖", "browser: {action} {target}", "acting", 0.9),
                       _b("📄", "reading the page back", "working", 1.0)),
    "http_request": (_b("📡", "{method} {url}", "acting", 0.9),
                     _b("⏳", "waiting for the response", "working", 1.1)),
    "list_windows": (_b("🪟", "enumerating open windows", "perceiving", 0.8),),
    "close_window": (_b("🪟", "finding '{title}'", "perceiving", 0.7),
                     _b("✖", "closing it", "acting", 0.6)),
    "snap_window": (_b("🪟", "snapping the window {direction}", "acting", 0.9),),
    "tile_windows": (_b("🧩", "arranging windows ({layout})", "acting", 1.0),),
    "focus_window": (_b("🪟", "raising '{title}'", "acting", 0.8),),

    # -- files ----------------------------------------------------------- #
    "read_file": (_b("📄", "reading {path}", "perceiving", 0.7),),
    "read_document": (_b("📄", "parsing {path}", "perceiving", 1.1),
                      _b("📖", "extracting its text", "working", 1.1)),
    "write_file": (_b("✍", "writing {path}", "acting", 0.9),),
    "write_files": (_b("✍", "writing {n} files", "acting", 1.0),),
    "edit_file": (_b("✂", "editing {path}", "acting", 0.9),),
    "make_dir": (_b("📁", "creating {path}", "acting", 0.7),),
    "list_dir": (_b("📁", "listing {path}", "perceiving", 0.7),),
    "find_files": (_b("🔍", "searching {root} for '{pattern}'", "perceiving", 1.0),
                   _b("📄", "sifting the matches", "working", 0.9)),
    "download_file": (_b("⬇", "downloading {url}", "acting", 1.2),
                      _b("💾", "saving to disk", "working", 0.9)),
    "copy_file": (_b("📄", "copying {src}", "acting", 0.9),),
    "move_file": (_b("📄", "moving {src}", "acting", 0.9),),
    "delete_file": (_b("🗑", "sending {path} to the Recycle Bin", "acting", 0.8),),
    "convert_file": (_b("🔄", "converting {source} → {target_format}",
                        "working", 1.1),),
    "archive_intel": (_b("🗜", "{op} on {path}", "working", 1.0),),

    # -- coding ---------------------------------------------------------- #
    "synthesize_tool": (_b("🧠", "designing '{name}'", "thinking", 0.9),
                        _b("🧪", "testing the new tool", "verifying", 1.0),
                        _b("💾", "remembering it", "working", 0.7)),
    "execute_synthesized_tool": (_b("🧰", "loading '{name}'", "working", 0.8),
                                 _b("⚙", "running it", "acting", 0.9)),
    "list_synthesized_tools": (_b("🧰", "scanning the synthesized toolbox",
                                  "perceiving", 0.8),),
    "agent": (_b("🤝", "briefing the {name} specialist", "planning", 0.9),
              _b("📡", "waiting for its report", "working", 1.2)),
    "agent_swarm": (_b("🤝", "dispatching {n} specialists", "planning", 0.9),
                    _b("📡", "collecting their reports", "working", 1.2)),
    "code_task": (_b("🧑‍💻", "planning the build", "planning", 0.8),
                  _b("📝", "writing the code", "acting", 1.0),
                  _b("🧪", "testing it", "verifying", 1.0)),
    "code_intel": (_b("🌳", "parsing {path} ({op})", "working", 1.0),),
    "db_query": (_b("🗄", "db: {op}", "working", 0.8),
                 _b("🧮", "running the statement", "working", 0.9)),
    "git_intel": (_b("🌿", "git {op} in {path}", "working", 0.9),),
    "self_upgrade": (_b("🧬", "snapshotting my own source", "working", 0.8),
                     _b("✏", "patching it", "acting", 1.0),
                     _b("🧪", "verifying the change", "verifying", 1.1)),
    "data_validate": (_b("🧾", "{op} the document", "working", 0.9),),
    "diff_patch": (_b("📐", "comparing {source} against {target}",
                      "working", 0.9),),
    "regex_intel": (_b("🔣", "regex {op}: /{pattern}/", "working", 0.9),),
    "api_mock": (_b("🎭", "mock server: {op} :{port}", "acting", 0.9),),
    "cron_intel": (_b("⏰", "cron {op}: {expr}", "working", 0.9),),

    # -- system ---------------------------------------------------------- #
    "run_command": (_b("⌨", "running: {command}", "acting", 1.0),
                    _b("⏳", "waiting for it to finish", "working", 1.0)),
    "python": (_b("🐍", "running a Python snippet", "acting", 1.0),
               _b("⏳", "waiting for it to finish", "working", 0.9)),
    "session_exec": (_b("🖥", "{op} in session '{name}'", "acting", 0.9),),
    "self_heal": (_b("🩹", "recovering: {strategy}", "healing", 1.0),),
    "daemon_rule": (_b("🛰", "daemon rules: {action}", "working", 0.9),),
    "hud_control": (_b("💠", "HUD: {action}", "working", 0.7),),
    "system_status": (_b("📊", "sampling CPU / memory / disk", "perceiving", 0.9),),
    "system_diagnostics": (_b("🩺", "auditing subsystems", "working", 1.0),
                           _b("🔧", "checking integrity", "verifying", 1.0)),
    "net_intel": (_b("🌐", "network {op}: {host}", "working", 1.0),),
    "process_intel": (_b("📈", "processes: {op}", "working", 1.0),),
    "web_search": (_b("🔍", "searching the web for '{query}'", "perceiving", 1.0),
                   _b("📚", "reading the results", "working", 0.9)),
    "extract_web_data": (_b("🕸", "scraping {url}", "perceiving", 1.0),
                         _b("🧮", "extracting {mode}", "working", 0.9)),
    "media_intel": (_b("🎵", "analysing {path}", "perceiving", 1.0),),
    "schedule_task": (_b("🗓", "scheduling '{schedule}'", "working", 0.9),),
    "media": (_b("🎚", "media: {op}", "acting", 0.7),),
    "notify": (_b("🔔", "notifying the user", "working", 0.7),),
    "take_screenshot": (_b("📸", "capturing the screen", "perceiving", 0.9),),
    "clipboard_read": (_b("📋", "reading the clipboard", "perceiving", 0.7),),
    "clipboard_write": (_b("📋", "copying to the clipboard", "acting", 0.7),),
    "remember": (_b("🧠", "committing a memory", "working", 0.8),
                 _b("🔗", "linking it in the knowledge graph", "working", 0.8)),
    "forget": (_b("🧠", "erasing '{target}'", "working", 0.8),),
    "memory_search": (_b("🧠", "recalling: {query}", "thinking", 1.0),),
    "notes": (_b("📓", "Obsidian: {action} {title}{query}", "working", 0.9),),
    "coordinates": (_b("📍", "coordinate memory: {action}", "working", 0.8),),
    "graph_query": (_b("🕸", "tracing '{entity}' in the knowledge graph",
                       "working", 1.0),),
    "voice_control": (_b("🎙", "voice: {action}", "working", 0.7),),
    "secret": (_b("🔐", "vault: {op}", "working", 0.8),),
    "see": (_b("👁", "looking at {source}", "perceiving", 1.1),
            _b("🧠", "interpreting what I see", "thinking", 1.1)),
    "camera": (_b("📷", "capturing a frame", "perceiving", 0.9),),
    "set_theme": (_b("🎨", "setting the theme to {theme}", "working", 0.7),),
    "crypto_intel": (_b("🔐", "crypto {op}", "working", 1.0),),

    # -- remote / connectors / mcp / skills ------------------------------- #
    "remote_task": (_b("📱", "linking to '{device}'", "working", 0.9),
                    _b("⏳", "waiting for the remote agent", "working", 1.2)),
    "connector": (_b("📬", "{service}: {op}", "working", 1.0),),
    "mcp": (_b("🔌", "MCP servers: {op}", "working", 0.9),),
    "mcp_call": (_b("🔌", "{server}.{tool}()", "acting", 1.0),),
    "skill": (_b("🎓", "skills: {action} {name}{query}", "working", 0.8),),

    # -- meta ------------------------------------------------------------ #
    "wait": (_b("⏳", "waiting {seconds}s", "working", 1.0),),
    "wait_for": (_b("⏳", "waiting for '{target}'", "working", 1.2),),
    "observe": (_b("👁", "re-reading the screen", "perceiving", 0.7),),
    "finish": (_b("🏁", "wrapping up", "working", 0.7),),
    "ask": (_b("❓", "asking the user", "working", 0.7),),
    "stop_session": (_b("⏻", "shutting the session down", "working", 0.9),),
}

#: Families whose members are not listed above individually would fall back to
#: this by category; kept as a plain map so a new action still animates.
_CATEGORY_FALLBACK = {
    "pointer": (_b("🎯", "{action} at the target", "acting", 0.9),),
    "keyboard": (_b("⌨", "{action}", "acting", 0.8),),
    "apps": (_b("🖥", "{action}: {name}{title}{url}", "acting", 0.9),),
    "files": (_b("📄", "{action}: {path}{src}{name}", "working", 0.9),),
    "coding": (_b("🧩", "{action}: {description}{path}{name}", "working", 1.0),),
    "system": (_b("⚙", "{action}", "working", 0.9),),
    "connectors": (_b("📬", "{action}: {service}", "working", 0.9),),
    "mcp": (_b("🔌", "{action}", "working", 0.9),),
    "remote": (_b("📱", "{action}: {device}", "working", 1.0),),
    "security": (_b("🔐", "{action}", "working", 0.9),),
    "meta": (_b("⚙", "{action}", "working", 0.8),),
}

_GENERIC = (_b("⚙", "{action}", "working", 0.9),)


def beats_for(action: str, category: str = "") -> tuple[Beat, ...]:
    """The script for ``action``: its own, its family's, or the generic one."""
    script = SCRIPTS.get(action)
    if script:
        return script
    family = _CATEGORY_FALLBACK.get(category or "")
    if family:
        return family
    return _GENERIC


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def label_for(beat: Beat, args: dict) -> str:
    """A beat's label with the action's arguments filled in.

    Substitution is done by hand rather than with ``str.format``: the values come
    straight from the model, so a missing or unusual key must render as something
    readable instead of raising inside a worker thread.
    """
    args = args if isinstance(args, dict) else {}

    def repl(match: re.Match) -> str:
        name = match.group(1)
        if name == "where":
            return _pointer_target(args)
        if name == "n":
            for key in ("keys", "files", "tasks"):
                value = args.get(key)
                if isinstance(value, (list, tuple)):
                    return str(len(value))
            return "0"
        value = args.get(name)
        if value is None or value == "" or value == {}:
            for alias in _ALIASES.get(name, ()):
                alternative = args.get(alias)
                if alternative is not None and alternative != "" \
                        and alternative != {}:
                    return _clip(alternative, 30)
            return _DEFAULTS.get(name, "…")
        return _clip(value, 30)

    return _clip(_TEMPLATE.sub(repl, beat.label), _LABEL_CHARS)


def _pointer_target(args: dict) -> str:
    """A pointer action's target, phrased the way the tool will resolve it.

    The three ways to name a click target (a saved name, an element id, raw
    pixels) are one concept to whoever is watching, so they render as one
    placeholder instead of a row of blanks.
    """
    for key in ("coord", "coordinate", "coord_name"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return f"the saved target '{value.strip()}'"
    element = args.get("element")
    if isinstance(element, (int, str)) and str(element).strip():
        return f"element {element}"
    x, y = args.get("x"), args.get("y")
    if x is not None and y is not None:
        return f"{_clip(x, 8)},{_clip(y, 8)}"
    return _DEFAULTS["target"]


# --------------------------------------------------------------------------- #
# The animator
# --------------------------------------------------------------------------- #

#: Where emitted beats go, beyond the built-in surfaces. Tests inject a list
#: here; a future UI can subscribe without this module knowing about it.
_SINK: Optional[Callable[[dict], None]] = None

_QUEUE: "queue.Queue[tuple]" = queue.Queue()
_STARTED = False
_START_LOCK = threading.Lock()
_GEN = 0


class _Run:
    """The beats of one in-flight action, and where the worker has got to."""

    __slots__ = ("gen", "action", "args", "beats", "index", "next_at", "speed",
                 "started_at")

    def __init__(self, gen: int, action: str, args: dict, beats: tuple[Beat, ...],
                 speed: float):
        self.gen = gen
        self.action = action
        self.args = args
        self.beats = beats
        self.index = 0
        self.speed = speed
        self.started_at = time.monotonic()
        self.next_at = self.started_at


class Animator:
    """The handle a tool call holds; only :meth:`finish` matters to it."""

    __slots__ = ("gen",)

    def __init__(self, gen: int = 0):
        self.gen = gen

    def finish(self, ok: bool = True, message: str = "") -> None:
        """Post the closing beat, carrying the tool's real result."""
        if not self.gen:
            return          # the no-op handle: nothing was ever started
        _QUEUE.put(("finish", self.gen, bool(ok), str(message or "")))

    def note(self, text: str, state: str = "working", glyph: str = "›") -> None:
        """Narrate a slow stretch from inside a tool (e.g. waiting on a window)."""
        if not self.gen:
            return
        _QUEUE.put(("note", self.gen, str(text or ""), state, glyph))


#: Handed back when nothing is listening: every method is a no-op, so a headless
#: or HUD-less run pays nothing for this module existing.
NULL = Animator(0)


def set_sink(sink: Optional[Callable[[dict], None]]) -> None:
    """Route emitted beats to ``sink`` as well (tests, alternative front-ends)."""
    global _SINK
    _SINK = sink


def enabled(cfg: Any = None) -> bool:
    """True when a beat would be seen by *something*.

    The user's own switch is checked first and wins outright: turning animations
    off must silence them everywhere, including any sink a front-end attached.
    """
    if cfg is not None:
        hud = getattr(cfg, "hud", None)
        if hud is not None and getattr(hud, "animations", True) is False:
            return False
    if _SINK is not None:
        return True
    return _has_audience()


def _has_audience() -> bool:
    """Any live surface: a running HUD overlay, the browser bridge, a TTY."""
    if _hud_overlay() is not None:
        return True
    try:
        from .. import browser_worker

        if getattr(browser_worker, "_bridge_installed", False):
            return True
    except Exception:
        pass
    try:
        return bool(sys.stdout and sys.stdout.isatty())
    except Exception:
        return False


def _hud_overlay() -> Any:
    """The live HUD overlay, if the HUD is actually running.

    Read through the package accessor rather than ``get_hud_controller()``: the
    animator must never *create* a controller (and its tkinter overlay) merely
    because a tool ran.
    """
    try:
        from ..hud import hud_overlay

        return hud_overlay()
    except Exception:
        return None


def _emit(beat: Beat, run: _Run, closing: bool = False) -> None:
    """Send one beat to every live surface. Never raises, never blocks long."""
    # Only a templated beat is rendered through the arguments, and a closing beat
    # never is: that label is the tool's own result, which may itself contain
    # braces (a JSON body, a code snippet) that must survive verbatim.
    if "{" in beat.label and not closing:
        label = label_for(beat, run.args)
    else:
        label = _clip(beat.label, _DETAIL_CHARS)
    detail = _clip(label, _DETAIL_CHARS)
    payload = {
        "action": run.action,
        "glyph": beat.glyph,
        "label": label,
        "state": beat.state,
        "pulse": beat.pulse,
        "closing": closing,
        "elapsed": time.monotonic() - run.started_at,
    }
    if _SINK is not None:
        try:
            _SINK(payload)
        except Exception:
            pass

    overlay = _hud_overlay()
    if overlay is not None:
        try:
            overlay.set_state(beat.state, detail=f"{beat.glyph} {detail}")
        except Exception:
            pass

    try:
        from .. import browser_worker

        if getattr(browser_worker, "_bridge_installed", False):
            browser_worker.emit("state", state=beat.state,
                                label=f"{beat.glyph} {detail}")
            browser_worker.emit("tool_beat", action=run.action, glyph=beat.glyph,
                                label=detail, state=beat.state,
                                pulse=beat.pulse, elapsed=round(payload["elapsed"], 2))
    except Exception:
        pass

    _terminal(beat, detail)


# --------------------------------------------------------------------------- #
# Terminal status line
# --------------------------------------------------------------------------- #

_TERM_LOCK = threading.Lock()
_term_drawn = False


def _terminal(beat: Beat, detail: str) -> None:
    """Draw the beat in place, the way ``log.spinner`` owns its line.

    Only on a TTY: any other stdout (pipes, tests, the browser worker's own
    event stream) must stay byte-clean.
    """
    global _term_drawn
    try:
        if not (sys.stdout and sys.stdout.isatty()):
            return
    except Exception:
        return
    with _TERM_LOCK:
        try:
            width = shutil.get_terminal_size((80, 24)).columns
            line = f"  {beat.glyph} {detail}"
            sys.stdout.write("\r" + line[: max(0, width - 1)].ljust(width - 1))
            sys.stdout.flush()
            _term_drawn = True
        except Exception:
            pass


def _terminal_clear() -> None:
    """Wipe the status line so the next log line starts clean."""
    global _term_drawn
    if not _term_drawn:
        return
    with _TERM_LOCK:
        try:
            width = shutil.get_terminal_size((80, 24)).columns
            sys.stdout.write("\r" + " " * max(0, width - 1) + "\r")
            sys.stdout.flush()
        except Exception:
            pass
        _term_drawn = False


# --------------------------------------------------------------------------- #
# Worker
# --------------------------------------------------------------------------- #

def _interval(beat: Beat, speed: float) -> float:
    seconds = BEAT_SECONDS * max(0.1, float(beat.weight))
    return max(MIN_BEAT_SECONDS, seconds / max(0.25, float(speed)))


def _speed_of(cfg: Any) -> float:
    hud = getattr(cfg, "hud", None) if cfg is not None else None
    try:
        return float(getattr(hud, "animation_speed", 1.0))
    except (TypeError, ValueError):
        return 1.0


def _worker() -> None:
    run: Optional[_Run] = None
    while True:
        timeout = 0.4
        if run is not None:
            timeout = max(0.02, run.next_at - time.monotonic())
        try:
            command = _QUEUE.get(timeout=timeout)
        except queue.Empty:
            command = None

        if command is not None:
            kind = command[0]
            if kind == "start":
                run = command[1]
                _fire(run, run.beats[0], hold=0.0)
            elif kind == "finish":
                _, gen, ok, message = command
                if run is not None and run.gen == gen:
                    _close(run, ok, message)
                    run = None
            elif kind == "note":
                _, gen, text, state, glyph = command
                if run is not None and run.gen == gen:
                    _emit(Beat(glyph, text, state, 0.0, 0.8), run)
            continue

        if run is None:
            continue
        if run.index < len(run.beats):
            _fire(run, run.beats[run.index])
        else:
            # Out of script but still running: refresh a heartbeat instead of
            # freezing on the last line, so a 30-second tool still looks alive.
            elapsed = time.monotonic() - run.started_at
            frame = _SPIN[int(elapsed * 8) % len(_SPIN)]
            _emit(Beat(frame, f"{run.action} · still working ({elapsed:.0f}s)",
                       "working", 0.0, 0.5), run)
            run.next_at = time.monotonic() + HEARTBEAT_SECONDS


def _fire(run: _Run, beat: Beat, hold: Optional[float] = None) -> None:
    _emit(beat, run)
    run.index += 1
    seconds = _interval(beat, run.speed) if hold is None else hold
    run.next_at = time.monotonic() + max(MIN_BEAT_SECONDS, seconds)


def _close(run: _Run, ok: bool, message: str) -> None:
    """The closing beat: the tool's own result, never an invented one."""
    summary = _clip(message, _DETAIL_CHARS) or ("done" if ok else "failed")
    beat = Beat("✓" if ok else "⚠", summary, "success" if ok else "warning",
                0.0, 1.6 if ok else 1.0)
    _emit(beat, run, closing=True)
    _terminal_clear()


def _ensure_worker() -> None:
    global _STARTED
    if _STARTED:
        return
    with _START_LOCK:
        if _STARTED:
            return
        threading.Thread(target=_worker, daemon=True,
                         name="jarvis-choreography").start()
        _STARTED = True


def begin(action: str, args: dict, cfg: Any = None, category: str = "") -> Animator:
    """Start narrating ``action``; the caller must call ``finish`` on the result.

    Costs a queue put when something is watching and two lookups when nothing is:
    this is on the path of every tool call, so it must never be the reason a tool
    feels slow.
    """
    global _GEN
    if not enabled(cfg):
        return NULL
    if not category:
        try:
            from ..tools.schema import ACTIONS_BY_NAME

            declared = ACTIONS_BY_NAME.get(action)
            category = declared.category if declared is not None else ""
        except Exception:
            category = ""
    _ensure_worker()
    _GEN += 1
    run = _Run(_GEN, action, args if isinstance(args, dict) else {},
               beats_for(action, category), _speed_of(cfg))
    _QUEUE.put(("start", run))
    return Animator(_GEN)


def note(text: str, state: str = "working", glyph: str = "›") -> None:
    """Narrate a slow stretch of the *current* action from inside a tool.

    Used by a tool that knows it is about to wait on something slow (a launched
    app's window, a remote device) so the wait is shown instead of silently
    sitting on the previous line.
    """
    if not _GEN:
        return
    _QUEUE.put(("note", _GEN, str(text or ""), state, glyph))
