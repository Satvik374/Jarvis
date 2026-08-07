"""Execute a parsed action against the live desktop.

The agentic loop hands us ``(action_name, args, observation)`` and we turn it
into real mouse/keyboard/OS effects, returning a human-readable result string
that gets fed back to the model as the outcome of its action.

Pointer targets are resolved here: an ``element`` id is looked up in the current
observation to get an exact centre pixel; otherwise raw ``x``/``y`` are used.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from . import mouse, keyboard, apps, files, system
from .schema import ACTIONS_BY_NAME
from ..config import Config
from ..perception.elements import Observation


@dataclass
class ActionResult:
    ok: bool
    message: str
    # ``needs_observe`` tells the loop the screen likely changed.
    needs_observe: bool = True
    # Set for terminal actions (finish / ask).
    finished: bool = False
    ask: str | None = None


class UnknownAction(Exception):
    pass


def execute(name: str, args: dict[str, Any], obs: Observation,
            cfg: Config) -> ActionResult:
    if name not in ACTIONS_BY_NAME:
        raise UnknownAction(name)
    args = args or {}
    handler = _HANDLERS.get(name)
    if handler is None:  # pragma: no cover - schema/registry mismatch guard
        raise UnknownAction(name)
    return handler(args, obs, cfg)


# --------------------------------------------------------------------------- #
# target resolution
# --------------------------------------------------------------------------- #

def _norm_to_pixels(x: float, y: float, obs: Observation, cfg) -> tuple[float, float]:
    """Gemini vision emits coordinates normalized to 0-1000 (its trained
    convention, reinforced by our system instruction). Convert a raw pair back
    to real screen pixels. Values above 1000 are already pixels and pass
    through untouched."""
    if cfg is None or not (cfg.brain.backend in {"gemini", "vertex"}
                           and cfg.brain.use_vision):
        return x, y
    sw, sh = obs.screen_size
    if sw <= 1000 and sh <= 1000:      # tiny screen: spaces are ambiguous
        return x, y
    if x <= 1000 and y <= 1000:
        return round(x * sw / 1000), round(y * sh / 1000)
    return x, y


def _resolve_point(args: dict, obs: Observation, cfg=None,
                   el_key: str = "element", x_key: str = "x",
                   y_key: str = "y") -> tuple:
    """Resolve a pointer target to screen pixels.

    Returns ``(point, error)`` - exactly one is set. The error string is
    model-facing and names the exact problem, because a stale element id and a
    corrupt coordinate need DIFFERENT corrections and the old generic message
    ("needs a valid element id or x,y") left the model retrying blind.
    """
    if args.get(el_key) is not None:
        try:
            el = obs.by_id(int(args[el_key]))
        except (TypeError, ValueError):
            return None, f"'{args[el_key]}' is not a valid element id"
        if el is not None:
            return el.center, ""
        ids = [e.id for e in obs.elements]
        rng = f"0-{max(ids)}" if ids else "none on screen"
        return None, (f"element {args[el_key]} is NOT in the current element "
                      f"list (valid ids: {rng}). The list is rebuilt every "
                      f"turn - use an id from the list shown THIS turn.")
    if args.get(x_key) is not None and args.get(y_key) is not None:
        try:
            fx, fy = float(args[x_key]), float(args[y_key])
        except (TypeError, ValueError):
            return None, (f"coordinates ({args[x_key]},{args[y_key]}) are "
                          f"not numbers")
        # A raw pair that EXACTLY matches an element's centre was copied from
        # the element list (models do this despite rule 11). The intent is
        # unambiguous - that element - and normalizing the pair instead would
        # land the click somewhere else entirely.
        for el in obs.elements:
            if el.center == (round(fx), round(fy)):
                return el.center, ""
        fx, fy = _norm_to_pixels(fx, fy, obs, cfg)
        raw_x, raw_y = int(fx), int(fy)
        # Snap to the nearest element if one is within 50px — the model's
        # raw coordinate guesses from vision are often slightly off, but
        # element centres from UIA are pixel-perfect.
        best_el = None
        best_dist = 50  # snap radius in pixels
        for el in obs.elements:
            cx, cy = el.center
            dist = ((cx - raw_x) ** 2 + (cy - raw_y) ** 2) ** 0.5
            if dist < best_dist:
                best_dist = dist
                best_el = el
        if best_el is not None:
            return best_el.center, ""
        # Reject off-screen/garbage coordinates: a huge value overflows the
        # Win32 C int in SetCursorPos ("argument 2: int too long to convert")
        # and kills the whole run.
        sw, sh = obs.screen_size
        if 0 <= raw_x < sw and 0 <= raw_y < sh:
            return (raw_x, raw_y), ""
        return None, (f"({args[x_key]},{args[y_key]}) lands outside the "
                      f"{sw}x{sh} screen - the value looks corrupted. Do NOT "
                      f"retry it; click by element id from the list instead.")
    return None, ("no target given - pass an element id from the list "
                  "(preferred) or on-screen x,y")


def _num(args: dict, key: str, default: float, lo: float, hi: float) -> float:
    """Clamped numeric arg that tolerates what models actually emit.

    A JSON ``null`` for an optional param, or a stray unit ("3s", "down"),
    otherwise reaches ``int()``/``float()`` and raises - costing a whole step
    to the loop's crash-recovery path and feeding the stuck-action guard.
    """
    raw = args.get(key)
    if raw is None or isinstance(raw, bool):
        return default
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return default
    if val != val:                 # NaN survives float() and breaks min/max
        return default
    return max(lo, min(hi, val))


def _target_desc(args: dict, obs: Observation, el_key: str = "element",
                 x_key: str = "x", y_key: str = "y") -> str:
    """Model-facing echo of a resolved target. Element clicks echo the label
    (confirms WHAT was hit). Raw clicks echo the model's OWN values - never
    the translated screen pixels: the model copies pixels from RESULT into its
    next click, where values <=1000 get re-read as Gemini-normalized and land
    somewhere else entirely (the root cause of most stuck-loop runs)."""
    if args.get(el_key) is not None:
        try:
            el = obs.by_id(int(args[el_key]))
        except (TypeError, ValueError):
            el = None
        if el is not None:
            name = (el.name or "").strip().replace("\n", " ")[:40]
            label = f' "{name}"' if name else ""
            return f"element [{el.id}] {el.role}{label}"
    return f"({args.get(x_key)},{args.get(y_key)})"


# --------------------------------------------------------------------------- #
# handlers
# --------------------------------------------------------------------------- #

def _h_click(args, obs, cfg):
    pt, err = _resolve_point(args, obs, cfg)
    if pt is None:
        return ActionResult(False, f"click failed: {err}")
    count = int(_num(args, "count", 1, 1, 10))
    mouse.click(*pt, clicks=count)
    return ActionResult(True, "left-clicked " + _target_desc(args, obs)
                        + (f" x{count}" if count > 1 else ""))


def _h_double_click(args, obs, cfg):
    pt, err = _resolve_point(args, obs, cfg)
    if pt is None:
        return ActionResult(False, f"double_click failed: {err}")
    mouse.double_click(*pt)
    return ActionResult(True, "double-clicked " + _target_desc(args, obs))


def _h_triple_click(args, obs, cfg):
    pt, err = _resolve_point(args, obs, cfg)
    if pt is None:
        return ActionResult(False, f"triple_click failed: {err}")
    mouse.triple_click(*pt)
    return ActionResult(True, "triple-clicked " + _target_desc(args, obs))


def _h_right_click(args, obs, cfg):
    pt, err = _resolve_point(args, obs, cfg)
    if pt is None:
        return ActionResult(False, f"right_click failed: {err}")
    mouse.right_click(*pt)
    return ActionResult(True, "right-clicked " + _target_desc(args, obs))


def _h_move(args, obs, cfg):
    pt, err = _resolve_point(args, obs, cfg)
    if pt is None:
        return ActionResult(False, f"move failed: {err}")
    mouse.move(*pt)
    return ActionResult(True, "moved mouse to " + _target_desc(args, obs),
                        needs_observe=False)


def _h_drag(args, obs, cfg):
    src, err_s = _resolve_point(args, obs, cfg, "from_element", "x1", "y1")
    dst, err_d = _resolve_point(args, obs, cfg, "to_element", "x2", "y2")
    if src is None or dst is None:
        return ActionResult(False, "drag failed: " + (err_s or err_d))
    mouse.drag(*src, *dst)
    return ActionResult(True, "dragged "
                        + _target_desc(args, obs, "from_element", "x1", "y1")
                        + " -> "
                        + _target_desc(args, obs, "to_element", "x2", "y2"))


def _h_scroll(args, obs, cfg):
    # Clamp: dy*120 goes raw into mouse_event's C int dwData - a huge model
    # value overflows it the same way as bad coordinates.
    dy = int(_num(args, "dy", 3, -50, 50))
    dx = int(_num(args, "dx", 0, -50, 50))
    return ActionResult(True, mouse.scroll(dy, dx))


def _h_type(args, obs, cfg):
    text = str(args.get("text", ""))
    if not text:
        return ActionResult(False, "type needs text")
    return ActionResult(True, keyboard.type_text(text))


def _h_press(args, obs, cfg):
    keys = str(args.get("keys", ""))
    if not keys:
        return ActionResult(False, "press needs keys")
    return ActionResult(True, keyboard.press(keys))


def _h_key_sequence(args, obs, cfg):
    keys = args.get("keys", [])
    if not keys:
        return ActionResult(False, "key_sequence needs a non-empty 'keys' list")
    return ActionResult(True, keyboard.press_sequence(keys))


def _h_open_app(args, obs, cfg):
    name = str(args.get("name", ""))
    if not name:
        return ActionResult(False, "open_app needs a name")
    return ActionResult(True, apps.open_app(name))


def _h_focus_window(args, obs, cfg):
    return ActionResult(True, apps.focus_window(str(args.get("title", ""))))


def _h_open_url(args, obs, cfg):
    return ActionResult(True, system.open_url(str(args.get("url", ""))))


def _h_read_url(args, obs, cfg):
    url = str(args.get("url", ""))
    if not url.strip():
        return ActionResult(False, "read_url needs a url", needs_observe=False)
    return ActionResult(
        True, system.read_url(url, int(args.get("max_chars", 8000) or 8000)),
        needs_observe=False)


def _h_list_windows(args, obs, cfg):
    titles = apps.list_windows()
    if not titles:
        return ActionResult(True, "no open windows found", needs_observe=False)
    return ActionResult(
        True, "open windows:\n" + "\n".join(f"- {t}" for t in titles[:40]),
        needs_observe=False)


def _h_close_window(args, obs, cfg):
    return ActionResult(True, apps.close_window(str(args.get("title", ""))))


def _h_wait_for(args, obs, cfg):
    target = str(args.get("target", "")).strip().lower()
    if not target:
        return ActionResult(False, "wait_for needs a 'target' substring",
                            needs_observe=False)
    try:
        timeout = max(1.0, min(30.0, float(args.get("timeout", 10.0) or 10.0)))
    except (TypeError, ValueError):
        timeout = 10.0
    from ..perception import elements as elem_mod
    deadline = time.time() + timeout
    while True:
        # Window titles first (cheap), then a full perception pass (thorough).
        for t in apps.list_windows():
            if target in t.lower():
                return ActionResult(True, f"window '{t}' is present")
        try:
            cur = elem_mod.observe(max_elements=cfg.perception.max_elements,
                                   use_uia=cfg.perception.use_uia,
                                   use_ocr=cfg.perception.use_ocr)
            for el in cur.elements:
                if target in (el.name or "").lower():
                    return ActionResult(
                        True, f"element '{el.name.strip()[:60]}' is present")
        except Exception:
            pass                     # perception hiccup: keep polling
        if time.time() >= deadline:
            return ActionResult(
                False, f"'{args.get('target')}' did not appear within "
                       f"{timeout:.0f}s")
        time.sleep(1.0)


def _h_run_command(args, obs, cfg):
    try:
        timeout = max(5, min(600, int(args.get("timeout", 60) or 60)))
    except (TypeError, ValueError):
        timeout = 60
    cwd = str(args.get("cwd", "")).strip() or None
    if cwd:
        from .files import _expand
        cwd = str(_expand(cwd))
    return ActionResult(
        True,
        system.run_command(str(args.get("command", "")),
                           blocked=cfg.safety.blocked_command_patterns,
                           timeout=timeout, cwd=cwd),
        needs_observe=False,
    )


def _h_python(args, obs, cfg):
    code = str(args.get("code", ""))
    if not code.strip():
        return ActionResult(False, "python needs code to run", needs_observe=False)
    cwd = str(args.get("cwd", "")).strip() or None
    if cwd:
        from .files import _expand
        cwd = str(_expand(cwd))
    return ActionResult(True, system.run_python(code, args.get("timeout", 60), cwd),
                        needs_observe=False)


def _h_http_request(args, obs, cfg):
    url = str(args.get("url", ""))
    if not url.strip():
        return ActionResult(False, "http_request needs a url", needs_observe=False)
    return ActionResult(
        True,
        system.http_request(
            str(args.get("method", "GET") or "GET"), url,
            headers=args.get("headers"), params=args.get("params"),
            json_body=args.get("json_body"), data=args.get("data"),
            timeout=args.get("timeout", 30)),
        needs_observe=False)


def _h_download_file(args, obs, cfg):
    url = str(args.get("url", ""))
    if not url.strip():
        return ActionResult(False, "download_file needs a url", needs_observe=False)
    msg = files.download_file(url, str(args.get("dest", "")),
                              allow=cfg.safety.allow_paths,
                              max_mb=args.get("max_mb", 500))
    return ActionResult(msg.startswith("downloaded"), msg, needs_observe=False)


def _h_read_file(args, obs, cfg):
    path = str(args.get("path", ""))
    from pathlib import Path
    if Path(path).name == "memory.txt":
        proj_root = Path(__file__).resolve().parent.parent.parent
        path = str(proj_root / "memory.txt")
    return ActionResult(True, files.read_file(path),
                        needs_observe=False)


def _h_read_document(args, obs, cfg):
    path = str(args.get("path", ""))
    if not path.strip():
        return ActionResult(False, "read_document needs a path", needs_observe=False)
    from . import documents
    return ActionResult(True, documents.read_document(
        path, args.get("max_chars", 20000)), needs_observe=False)


def _h_write_file(args, obs, cfg):
    path = str(args.get("path", ""))
    from pathlib import Path
    if Path(path).name == "memory.txt":
        proj_root = Path(__file__).resolve().parent.parent.parent
        path = str(proj_root / "memory.txt")
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(str(args.get("content", "")), encoding="utf-8")
            return ActionResult(True, f"wrote {len(args.get('content', ''))} chars to memory.txt",
                                needs_observe=False)
        except Exception as exc:
            return ActionResult(False, f"could not write memory: {exc}",
                                needs_observe=False)
    return ActionResult(
        True,
        files.write_file(path, str(args.get("content", "")),
                         allow=cfg.safety.allow_paths),
        needs_observe=False,
    )


def _h_write_files(args, obs, cfg):
    items = args.get("files")
    if not isinstance(items, list) or not items:
        return ActionResult(False, "write_files needs a non-empty 'files' "
                            "list of {path, content} objects",
                            needs_observe=False)
    lines, ok = [], True
    for i, it in enumerate(items[:50]):    # sane cap; 50 files is a big scaffold
        if not isinstance(it, dict) or not str(it.get("path", "")).strip():
            lines.append(f"- item {i}: invalid (needs 'path' and 'content')")
            ok = False
            continue
        msg = files.write_file(str(it["path"]), str(it.get("content", "")),
                               allow=cfg.safety.allow_paths)
        if not msg.startswith("wrote"):
            ok = False
        lines.append("- " + msg)
    return ActionResult(ok, f"write_files ({len(lines)} file(s)):\n"
                        + "\n".join(lines), needs_observe=False)


def _h_edit_file(args, obs, cfg):
    msg = files.edit_file(str(args.get("path", "")), str(args.get("old", "")),
                          str(args.get("new", "")),
                          allow=cfg.safety.allow_paths)
    return ActionResult(msg.startswith("edited"), msg, needs_observe=False)


def _h_code_task(args, obs, cfg):
    desc = str(args.get("description", "")).strip()
    if not desc:
        return ActionResult(False, "code_task needs a description",
                            needs_observe=False)
    import re
    from pathlib import Path
    from ..agent.brain import make_brain
    from ..agent.coder import Coder
    from .files import _expand
    wd = str(args.get("workdir", "")).strip()
    if wd:
        wd = str(_expand(wd))
    else:
        slug = re.sub(r"[^a-z0-9]+", "-", desc.lower())[:40].strip("-") or "project"
        wd = str(Path.home() / "JarvisProjects" / slug)
    from ..utils import logging as log
    log.rule(f"coder: {desc[:60]}", "magenta")
    # ponytail: fresh brain per call - free for API backends; cache it if the
    # heavyweight 'hf' backend ever becomes the coding brain.
    coder = Coder(make_brain(cfg.brain), cfg)
    msg, is_ask = coder.run(desc, wd)
    if is_ask:
        return ActionResult(True, msg, needs_observe=False, finished=True,
                            ask=msg)
    return ActionResult(True, msg, needs_observe=False)


def _h_self_upgrade(args, obs, cfg):
    """Guarded self-modification: snapshot Jarvis's own source, let the coding
    engine change it, verify (syntax + import), roll back automatically if the
    new code is broken. Changes take effect on the next restart."""
    desc = str(args.get("description", "")).strip()
    if not desc:
        return ActionResult(False, "self_upgrade needs a description",
                            needs_observe=False)
    import shutil
    import subprocess
    import sys
    from pathlib import Path
    from ..agent.brain import make_brain
    from ..agent.coder import Coder
    from ..utils import logging as log

    root = Path(__file__).resolve().parent.parent.parent
    targets = ("jarvis", "run.py", "config.yaml")
    backup = root / ".self_backups" / time.strftime("%Y%m%d-%H%M%S")
    try:
        backup.mkdir(parents=True, exist_ok=True)
        for t in targets:
            src = root / t
            if src.is_dir():
                shutil.copytree(src, backup / t,
                                ignore=shutil.ignore_patterns("__pycache__"))
            elif src.is_file():
                shutil.copy2(src, backup / t)
    except Exception as exc:
        return ActionResult(False, f"refused: could not snapshot my source "
                            f"before changing it ({exc})", needs_observe=False)

    log.rule(f"self-upgrade: {desc[:60]}", "magenta")
    task = (
        "You are modifying JARVIS'S OWN SOURCE CODE - the assistant that is "
        "running you right now. Change request:\n" + desc + "\n\n"
        "Constraints:\n"
        "  * Make the SMALLEST change that fulfils the request; keep the "
        "existing style and the JSON action contract intact.\n"
        "  * Key files: jarvis/tools/schema.py (action definitions), "
        "jarvis/tools/registry.py (action handlers), jarvis/agent/loop.py "
        "(main loop), jarvis/agent/prompts.py (system prompt), "
        "jarvis/agent/brain.py (LLM backends).\n"
        "  * A new action must be added to BOTH schema.py and registry.py.\n"
        "  * Use edit_file for changes - never rewrite a whole existing file.\n"
        "  * Do not touch .self_backups, dataset/ or training/.\n"
        f"  * Verify your work with run_command: \"{sys.executable}\" -m "
        f"compileall -q jarvis")
    coder = Coder(make_brain(cfg.brain), cfg)
    msg, is_ask = coder.run(task, str(root))
    if is_ask:
        return ActionResult(True, msg, needs_observe=False, finished=True,
                            ask=msg)

    # Independent verification: every file must compile AND the core modules
    # must import cleanly in a FRESH interpreter (this one has the old code).
    err = ""
    for cmd in ([sys.executable, "-m", "compileall", "-q", "jarvis", "run.py"],
                [sys.executable, "-c",
                 "import jarvis.agent.loop, jarvis.tools.registry, "
                 "jarvis.console"]):
        try:
            p = subprocess.run(cmd, cwd=str(root), capture_output=True,
                               text=True, timeout=180)
        except Exception as exc:
            err = str(exc)
            break
        if p.returncode != 0:
            err = (p.stderr or p.stdout or "verification failed").strip()[-400:]
            break

    if err:
        for t in targets:                       # roll back to the snapshot
            src, dst = backup / t, root / t
            if src.is_dir():
                shutil.rmtree(dst, ignore_errors=True)
                shutil.copytree(src, dst)
            elif src.is_file():
                shutil.copy2(src, dst)
        return ActionResult(False, f"self-upgrade FAILED verification and was "
                            f"rolled back, I am unchanged (backup: {backup}). "
                            f"Error: {err}", needs_observe=False)
    return ActionResult(True, f"self-upgrade verified: {msg} (snapshot kept "
                        f"at {backup}; the change takes effect the next time "
                        f"Jarvis restarts)", needs_observe=False)


def _h_agent(args, obs, cfg):
    name = str(args.get("name", "")).strip().lower()
    task = str(args.get("task", "")).strip()
    if not task:
        return ActionResult(False, "agent needs a 'task'", needs_observe=False)
    if name == "coder":     # the coder keeps its workdir machinery
        return _h_code_task({"description": task}, obs, cfg)
    from ..agent import subagent
    spec = subagent.available().get(name)
    if spec is None:
        names = ", ".join(sorted(list(subagent.available()) + ["coder"]))
        return ActionResult(False, f"unknown agent '{name}' - available: {names}",
                            needs_observe=False)
    from ..agent.brain import make_brain
    from ..utils import logging as log
    log.rule(f"{name}: {task[:60]}", "magenta")
    # ponytail: fresh brain per call - free for API backends; cache it if the
    # heavyweight 'hf' backend ever becomes a sub-agent brain.
    msg, is_ask = subagent.run_agent(spec, make_brain(cfg.brain), cfg, task)
    if is_ask:
        return ActionResult(True, msg, needs_observe=False, finished=True,
                            ask=msg)
    return ActionResult(True, f"[{name} report] {msg}", needs_observe=False)


def _h_find_files(args, obs, cfg):
    return ActionResult(
        True,
        files.find_files(str(args.get("pattern", "")),
                         str(args.get("root", "~") or "~"),
                         int(args.get("max_results", 40) or 40)),
        needs_observe=False)


def _h_copy_file(args, obs, cfg):
    msg = files.copy_file(str(args.get("src", "")), str(args.get("dst", "")),
                          allow=cfg.safety.allow_paths)
    return ActionResult(msg.startswith("copied"), msg, needs_observe=False)


def _h_move_file(args, obs, cfg):
    msg = files.move_file(str(args.get("src", "")), str(args.get("dst", "")),
                          allow=cfg.safety.allow_paths)
    return ActionResult(msg.startswith("moved"), msg, needs_observe=False)


def _h_delete_file(args, obs, cfg):
    msg = files.delete_path(str(args.get("path", "")),
                            allow=cfg.safety.allow_paths)
    return ActionResult(msg.startswith("sent to"), msg, needs_observe=False)


def _h_list_dir(args, obs, cfg):
    return ActionResult(True, files.list_dir(str(args.get("path", "."))),
                        needs_observe=False)


def _h_make_dir(args, obs, cfg):
    return ActionResult(
        True,
        files.make_dir(str(args.get("path", "")), allow=cfg.safety.allow_paths),
        needs_observe=False,
    )


def _h_system_status(args, obs, cfg):
    return ActionResult(True, system.system_status(), needs_observe=False)


def _h_web_search(args, obs, cfg):
    query = str(args.get("query", ""))
    if not query:
        return ActionResult(False, "web_search needs a query", needs_observe=False)
    results = system.web_search(query, int(args.get("max_results", 5) or 5))
    return ActionResult(True, results, needs_observe=False)


def _h_schedule_task(args, obs, cfg):
    from .. import scheduler
    sched = scheduler.get_default()
    if sched is None:
        return ActionResult(False, "scheduling is only available in the "
                            "interactive console session", needs_observe=False)
    schedule = str(args.get("schedule", ""))
    command = str(args.get("command", ""))
    if not schedule or not command:
        return ActionResult(False, "schedule_task needs 'schedule' and 'command'",
                            needs_observe=False)
    try:
        job = sched.add(schedule, command)
    except scheduler.ScheduleError as exc:
        return ActionResult(False, str(exc), needs_observe=False)
    return ActionResult(True, f"scheduled job {job.id}: {job.spec} -> {command!r}",
                        needs_observe=False)


def _h_media(args, obs, cfg):
    op = str(args.get("op", "")).strip()
    if not op:
        return ActionResult(False, "media needs an 'op'", needs_observe=False)
    value = args.get("value")
    try:
        value = int(value) if value is not None else None
    except (TypeError, ValueError):
        value = None
    msg = system.media_control(op, value)
    ok = not msg.startswith(("unknown", "set_volume needs"))
    return ActionResult(ok, msg, needs_observe=False)


def _h_notify(args, obs, cfg):
    msg = system.notify(str(args.get("message", "")),
                        str(args.get("title", "JARVIS") or "JARVIS"))
    return ActionResult(msg.startswith("notification shown"), msg,
                        needs_observe=False)


def _h_connector(args, obs, cfg):
    from . import connectors
    service = str(args.get("service", "")).strip()
    if not service:
        return ActionResult(False, "connector needs a 'service' (gmail, "
                            "discord or whatsapp)", needs_observe=False)
    try:
        msg = connectors.fetch(service, str(args.get("op", "")),
                               str(args.get("query", "") or ""),
                               str(args.get("target", "") or ""),
                               args.get("limit", 10))
    except connectors.ConnectorError as exc:
        return ActionResult(False, str(exc), needs_observe=False)
    return ActionResult(True, msg, needs_observe=False)


def _h_mcp(args, obs, cfg):
    from .. import mcp
    return ActionResult(True, mcp.manage(args), needs_observe=False)


def _h_mcp_call(args, obs, cfg):
    from .. import mcp
    server = str(args.get("server", "")).strip()
    tool = str(args.get("tool", "")).strip()
    if not server or not tool:
        return ActionResult(False, "mcp_call needs 'server' and 'tool'",
                            needs_observe=False)
    arguments = args.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}
    ok, msg = mcp.get_manager().call(server, tool, arguments)
    return ActionResult(ok, msg, needs_observe=False)


def _h_take_screenshot(args, obs, cfg):
    from pathlib import Path
    from ..perception import screen as screen_mod
    from .files import _expand, _within
    path = str(args.get("path", "")).strip()
    if not path:
        path = str(Path.home() / "Pictures"
                   / screen_mod.timestamped_name("screenshot"))
    p = _expand(path)
    if p.suffix == "":
        p = p.with_suffix(".png")
    if not _within(p, cfg.safety.allow_paths):
        return ActionResult(False, f"refused: {p} is outside allowed write "
                            "locations", needs_observe=False)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        screen_mod.capture().image.save(str(p))
        return ActionResult(True, f"screenshot saved to {p}",
                            needs_observe=False)
    except Exception as exc:
        return ActionResult(False, f"could not save screenshot: {exc}",
                            needs_observe=False)


def _h_clipboard_read(args, obs, cfg):
    return ActionResult(True, "clipboard: " + system.clipboard_read(),
                        needs_observe=False)


def _h_clipboard_write(args, obs, cfg):
    return ActionResult(True, system.clipboard_write(str(args.get("text", ""))),
                        needs_observe=False)


def _h_remember(args, obs, cfg):
    fact = str(args.get("fact", "")).strip()
    if not fact:
        return ActionResult(False, "remember needs a 'fact' parameter", needs_observe=False)
    cat = str(args.get("category", "fact")).strip()
    from ..agent.memory import remember_fact
    msg = remember_fact(fact=fact, category=cat)
    return ActionResult(True, msg, needs_observe=False)


def _h_forget(args, obs, cfg):
    target = str(args.get("target", "")).strip()
    if not target:
        return ActionResult(False, "forget needs a 'target' parameter", needs_observe=False)
    from ..agent.memory import forget_fact
    msg = forget_fact(target=target)
    return ActionResult(True, msg, needs_observe=False)


def _h_remote_task(args, obs, cfg):
    device = str(args.get("device", "")).strip()
    task = str(args.get("task", "")).strip()
    if not device or not task:
        return ActionResult(False, "remote_task needs both a device and task", needs_observe=False)
    try:
        timeout = args.get("timeout")
        timeout = int(timeout) if timeout is not None else None
    except (TypeError, ValueError):
        timeout = None
    from .. import remote
    try:
        ok, message = remote.send_task(cfg, device, task, timeout=timeout)
    except remote.RemoteError as exc:
        return ActionResult(False, str(exc), needs_observe=False)
    return ActionResult(ok, message, needs_observe=False)


def _h_wait(args, obs, cfg):
    secs = _num(args, "seconds", 1.0, 0.0, 10.0)
    time.sleep(secs)
    return ActionResult(True, f"waited {secs}s")


def _h_observe(args, obs, cfg):
    return ActionResult(True, "re-reading the screen", needs_observe=True)


def _h_finish(args, obs, cfg):
    return ActionResult(True, str(args.get("summary", "done")),
                        needs_observe=False, finished=True)


def _h_set_theme(args, obs, cfg):
    theme = str(args.get("theme", "arc")).strip().lower()
    return ActionResult(True, f"UI visual theme set to '{theme}'", needs_observe=False)


def _h_ask(args, obs, cfg):
    q = str(args.get("question", "Could you clarify?"))
    return ActionResult(True, q, needs_observe=False, finished=True, ask=q)


_HANDLERS = {
    "click": _h_click,
    "double_click": _h_double_click,
    "triple_click": _h_triple_click,
    "right_click": _h_right_click,
    "move": _h_move,
    "drag": _h_drag,
    "scroll": _h_scroll,
    "type": _h_type,
    "press": _h_press,
    "key_sequence": _h_key_sequence,
    "open_app": _h_open_app,
    "focus_window": _h_focus_window,
    "list_windows": _h_list_windows,
    "close_window": _h_close_window,
    "open_url": _h_open_url,
    "read_url": _h_read_url,
    "http_request": _h_http_request,
    "python": _h_python,
    "download_file": _h_download_file,
    "wait_for": _h_wait_for,
    "run_command": _h_run_command,
    "system_status": _h_system_status,
    "media": _h_media,
    "notify": _h_notify,
    "take_screenshot": _h_take_screenshot,
    "web_search": _h_web_search,
    "schedule_task": _h_schedule_task,
    "read_file": _h_read_file,
    "read_document": _h_read_document,
    "write_file": _h_write_file,
    "write_files": _h_write_files,
    "edit_file": _h_edit_file,
    "agent": _h_agent,
    "code_task": _h_code_task,
    "self_upgrade": _h_self_upgrade,
    "make_dir": _h_make_dir,
    "list_dir": _h_list_dir,
    "find_files": _h_find_files,
    "copy_file": _h_copy_file,
    "move_file": _h_move_file,
    "delete_file": _h_delete_file,
    "clipboard_read": _h_clipboard_read,
    "clipboard_write": _h_clipboard_write,
    "remember": _h_remember,
    "forget": _h_forget,
    "remote_task": _h_remote_task,
    "connector": _h_connector,
    "mcp": _h_mcp,
    "mcp_call": _h_mcp_call,
    "wait": _h_wait,
    "observe": _h_observe,
    "finish": _h_finish,
    "set_theme": _h_set_theme,
    "ask": _h_ask,
}
