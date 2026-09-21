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

from . import mouse, keyboard, apps, files, system, mouse_control, converter, code_intel, session_exec, diagnostics, web_extractor, db_query, git_intel, net_intel, media_intel, archive_intel, data_validate, crypto_intel, diff_patch, process_intel, regex_intel, api_mock, cron_intel, tool_synthesis
from .schema import ACTIONS_BY_NAME
from ..config import Config
from ..perception.elements import Observation
from ..utils.paths import project_root, state_root


@dataclass
class ActionResult:
    ok: bool
    message: str
    # ``needs_observe`` tells the loop the screen likely changed.
    needs_observe: bool = True
    # Set for terminal actions (finish / ask).
    finished: bool = False
    ask: str | None = None
    # A tool can attach an authenticated remote image to the next vision turn.
    image_path: str | None = None
    # Remote UI actions invalidate a previously attached device screenshot.
    clear_image: bool = False
    # Set by ``stop_session``: the agent asked for the session itself to end.
    # The loop stops the run on this - it is deliberately not ``finished``,
    # because a stop is not a completed task and must never be handed to the
    # verifier as one.
    stop_session: bool = False


class UnknownAction(Exception):
    pass


def execute(name: str, args: dict[str, Any], obs: Observation,
            cfg: Config) -> ActionResult:
    if name not in ACTIONS_BY_NAME:
        raise UnknownAction(name)
    args = args or {}
    handler = handler_for(name)
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
    try:
        from ..desktop import is_shadow_enabled, get_virtual_input
        if is_shadow_enabled():
            get_virtual_input().click(pt[0], pt[1], clicks=count)
            return ActionResult(True, "left-clicked " + _target_desc(args, obs)
                                + (f" x{count}" if count > 1 else "") + " (shadow workspace)")
    except Exception:
        pass

    mouse.click(*pt, clicks=count)
    return ActionResult(True, "left-clicked " + _target_desc(args, obs)
                        + (f" x{count}" if count > 1 else ""))


def _h_double_click(args, obs, cfg):
    pt, err = _resolve_point(args, obs, cfg)
    if pt is None:
        return ActionResult(False, f"double_click failed: {err}")
    try:
        from ..desktop import is_shadow_enabled, get_virtual_input
        if is_shadow_enabled():
            get_virtual_input().click(pt[0], pt[1], clicks=2)
            return ActionResult(True, "double-clicked " + _target_desc(args, obs) + " (shadow workspace)")
    except Exception:
        pass
    mouse.double_click(*pt)
    return ActionResult(True, "double-clicked " + _target_desc(args, obs))


def _h_triple_click(args, obs, cfg):
    pt, err = _resolve_point(args, obs, cfg)
    if pt is None:
        return ActionResult(False, f"triple_click failed: {err}")
    try:
        from ..desktop import is_shadow_enabled, get_virtual_input
        if is_shadow_enabled():
            get_virtual_input().click(pt[0], pt[1], clicks=3)
            return ActionResult(True, "triple-clicked " + _target_desc(args, obs) + " (shadow workspace)")
    except Exception:
        pass
    mouse.triple_click(*pt)
    return ActionResult(True, "triple-clicked " + _target_desc(args, obs))


def _h_right_click(args, obs, cfg):
    pt, err = _resolve_point(args, obs, cfg)
    if pt is None:
        return ActionResult(False, f"right_click failed: {err}")
    try:
        from ..desktop import is_shadow_enabled, get_virtual_input
        if is_shadow_enabled():
            get_virtual_input().click(pt[0], pt[1], button="right", clicks=1)
            return ActionResult(True, "right-clicked " + _target_desc(args, obs) + " (shadow workspace)")
    except Exception:
        pass
    mouse.right_click(*pt)
    return ActionResult(True, "right-clicked " + _target_desc(args, obs))


def _h_move(args, obs, cfg):
    pt, err = _resolve_point(args, obs, cfg)
    if pt is None:
        return ActionResult(False, f"move failed: {err}")
    try:
        from ..desktop import is_shadow_enabled
        if is_shadow_enabled():
            return ActionResult(True, "moved virtual cursor to " + _target_desc(args, obs) + " (shadow workspace)",
                                needs_observe=False)
    except Exception:
        pass
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
    try:
        from ..desktop import is_shadow_enabled, get_virtual_input
        if is_shadow_enabled():
            get_virtual_input().scroll(dy, dx)
            return ActionResult(True, f"scrolled dy={dy} (shadow workspace)")
    except Exception:
        pass
    return ActionResult(True, mouse.scroll(dy, dx))


def _h_mouse_control(args, obs, cfg):
    raw = args.get("enabled")
    if isinstance(raw, bool):
        enabled = raw
    elif isinstance(raw, str) and raw.strip().lower() in {"true", "on", "1"}:
        enabled = True
    elif isinstance(raw, str) and raw.strip().lower() in {"false", "off", "0"}:
        enabled = False
    else:
        return ActionResult(False, "mouse_control needs enabled=true or false",
                            needs_observe=False)
    camera = int(_num(args, "camera", 0, 0, 9))
    ok, message = mouse_control.set_enabled(enabled, camera_index=camera)
    return ActionResult(ok, message, needs_observe=False)


def _h_type(args, obs, cfg):
    text = str(args.get("text", ""))
    if not text:
        return ActionResult(False, "type needs text")
    try:
        from ..desktop import is_shadow_enabled, get_virtual_input
        if is_shadow_enabled():
            get_virtual_input().type_text(text)
            return ActionResult(True, f"typed '{text}' (shadow workspace)")
    except Exception:
        pass
    return ActionResult(True, keyboard.type_text(text))


def _h_press(args, obs, cfg):
    keys = str(args.get("keys", ""))
    if not keys:
        return ActionResult(False, "press needs keys")
    try:
        from ..desktop import is_shadow_enabled, get_virtual_input
        if is_shadow_enabled():
            get_virtual_input().press_key(keys)
            return ActionResult(True, f"pressed key '{keys}' (shadow workspace)")
    except Exception:
        pass
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


def _h_snap_window(args, obs, cfg):
    direction = str(args.get("direction", "maximize"))
    title = args.get("title")
    msg = apps.snap_window(direction, title=str(title) if title else None)
    return ActionResult(not msg.startswith("could not") and not msg.startswith("unknown"), msg)


def _h_tile_windows(args, obs, cfg):
    layout = str(args.get("layout", "side_by_side"))
    msg = apps.tile_windows(layout)
    return ActionResult(not msg.startswith("unknown"), msg)



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
    result = system.run_command(str(args.get("command", "")),
                                blocked=cfg.safety.blocked_command_patterns,
                                timeout=timeout, cwd=cwd)
    return ActionResult(result.ok, str(result), needs_observe=False)


def _h_python(args, obs, cfg):
    code = str(args.get("code", ""))
    if not code.strip():
        return ActionResult(False, "python needs code to run", needs_observe=False)
    cwd = str(args.get("cwd", "")).strip() or None
    if cwd:
        from .files import _expand
        cwd = str(_expand(cwd))
    result = system.run_python(code, args.get("timeout", 60), cwd)
    return ActionResult(result.ok, str(result), needs_observe=False)


def _h_session_exec(args, obs, cfg):
    op = str(args.get("op", "exec")).strip()
    command = str(args.get("command", ""))
    name = str(args.get("name", "default"))
    shell_type = str(args.get("shell_type", "powershell"))
    try:
        timeout = max(1, min(300, int(args.get("timeout", 30) or 30)))
    except (TypeError, ValueError):
        timeout = 30
    cwd = str(args.get("cwd", "")).strip() or None
    if cwd:
        from .files import _expand
        cwd = str(_expand(cwd))
    msg = session_exec.session_exec(
        op=op,
        command=command,
        name=name,
        shell_type=shell_type,
        timeout=timeout,
        cwd=cwd,
        blocked=cfg.safety.blocked_command_patterns,
        allow=cfg.safety.allow_paths,
    )
    return ActionResult(msg.ok, str(msg), needs_observe=False)


def _h_http_request(args, obs, cfg):
    url = str(args.get("url", ""))
    if not url.strip():
        return ActionResult(False, "http_request needs a url", needs_observe=False)
    return ActionResult(
        True,
        system.http_request(
            str(args.get("method", "GET") or "GET"), url,
            headers=args.get("headers"),
            params=args.get("params"),
            json_body=args.get("json_body"), data=args.get("data"),
            timeout=args.get("timeout", 30)),
        needs_observe=False)


def _h_extract_web_data(args, obs, cfg):
    url = str(args.get("url", "")).strip()
    html_content = str(args.get("html_content", "")).strip()
    mode = str(args.get("mode", "tables")).strip()
    output_format = str(args.get("output_format", "json")).strip()
    res = web_extractor.extract_web_data(
        url=url,
        html_content=html_content,
        mode=mode,
        output_format=output_format,
        allow=cfg.safety.allow_paths,
    )
    ok = not (res.startswith("failed to fetch") or res.startswith("unknown extract_web_data mode") or res.startswith("extract_web_data requires"))
    return ActionResult(ok, res, needs_observe=False)


def _h_media_intel(args, obs, cfg):
    path = str(args.get("path", "")).strip()
    op = str(args.get("op", "info")).strip()
    target = str(args.get("target", "")).strip()
    start_sec = float(args.get("start_sec", 0.0) or 0.0)
    end_sec = float(args.get("end_sec", 0.0) or 0.0)
    threshold_db = float(args.get("threshold_db", -40.0) or -40.0)
    res = media_intel.media_intel(
        op=op,
        path=path,
        target=target,
        start_sec=start_sec,
        end_sec=end_sec,
        threshold_db=threshold_db,
        allow=cfg.safety.allow_paths,
    )
    ok = not (res.startswith("file not found:") or res.startswith("unknown media_intel op") or res.startswith("invalid slice") or '"error":' in res)
    return ActionResult(ok, res, needs_observe=False)


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
        # The agent's memory is state, so it follows the state root - not the
        # directory this module happens to live in, or read_file would hand the
        # model a different file than the memory manager writes.
        path = str(state_root() / "memory.txt")
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
        path = str(state_root() / "memory.txt")
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

    root = project_root()
    targets = ("jarvis", "run.py", "config.yaml")
    # The source read below stays at the project root; the snapshot we take
    # before editing it is Jarvis's own state and follows the state root.
    backup = state_root() / ".self_backups" / time.strftime("%Y%m%d-%H%M%S")
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


def _h_agent_swarm(args, obs, cfg):
    tasks = args.get("tasks")
    if not tasks or not isinstance(tasks, list):
        return ActionResult(False, "agent_swarm requires a list of task objects: [{'name': '...', 'task': '...'}, ...]", needs_observe=False)

    timeout = float(args.get("timeout", 180) or 180)
    from ..agent import subagent
    from ..agent.brain import make_brain
    from ..utils import logging as log

    log.rule(f"Swarm: {len(tasks)} parallel subagents", "magenta")
    msg, is_ask = subagent.run_swarm(tasks, make_brain(cfg.brain), cfg, timeout=timeout)
    if is_ask:
        return ActionResult(True, msg, needs_observe=False, finished=True, ask=msg)
    return ActionResult(True, msg, needs_observe=False)


def _h_code_intel(args, obs, cfg):
    op = str(args.get("op", "symbols")).strip()
    path = str(args.get("path", "."))
    query = str(args.get("query", ""))
    max_depth = int(args.get("max_depth", 3) or 3)
    res = code_intel.code_intel(
        op=op,
        path=path,
        query=query,
        max_depth=max_depth,
        allow=cfg.safety.allow_paths,
    )
    ok = not (res.startswith("path not found:") or res.startswith("unknown code_intel op"))
    return ActionResult(ok, res, needs_observe=False)


def _h_db_query(args, obs, cfg):
    path = str(args.get("path", "")).strip()
    sql = str(args.get("sql", "")).strip()
    op = str(args.get("op", "query")).strip()
    params = args.get("params")
    if not isinstance(params, list) and params is not None:
        params = [params]
    limit = int(args.get("limit", 100) or 100)
    output_format = str(args.get("output_format", "table")).strip()
    res = db_query.db_query(
        path=path,
        sql=sql,
        op=op,
        params=params,
        limit=limit,
        output_format=output_format,
        allow=cfg.safety.allow_paths,
    )
    ok = not (res.startswith("database file not found:") or res.startswith("database query error:") or res.startswith("unknown db_query op"))
    return ActionResult(ok, res, needs_observe=False)


def _h_git_intel(args, obs, cfg):
    path = str(args.get("path", "."))
    op = str(args.get("op", "status")).strip()
    target = str(args.get("target", "")).strip()
    message = str(args.get("message", "")).strip()
    limit = int(args.get("limit", 10) or 10)
    staged = bool(args.get("staged", False))
    res = git_intel.git_intel(
        op=op,
        path=path,
        target=target,
        message=message,
        limit=limit,
        staged=staged,
        allow=cfg.safety.allow_paths,
    )
    ok = not (res.startswith("git error:") or res.startswith("path not found:") or res.startswith("unknown git_intel op"))
    return ActionResult(ok, res, needs_observe=False)



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


def _h_convert_file(args, obs, cfg):
    source = str(args.get("source", ""))
    target = str(args.get("target", ""))
    target_format = str(args.get("target_format", ""))
    options = args.get("options")
    if isinstance(options, str):
        try:
            import json
            options = json.loads(options)
        except Exception:
            options = {}
    elif not isinstance(options, dict):
        options = {}
    msg = converter.convert_file(
        source=source,
        target=target,
        target_format=target_format,
        options=options,
        allow=cfg.safety.allow_paths,
    )
    ok = not (msg.startswith("refused:") or msg.startswith("source file not found:") or msg.startswith("could not convert") or msg.startswith("unsupported"))
    return ActionResult(ok, msg, needs_observe=False)


def _h_archive_intel(args, obs, cfg):
    path = str(args.get("path", "")).strip()
    op = str(args.get("op", "list")).strip()
    target = str(args.get("target", "")).strip()
    archive_format = str(args.get("archive_format", "zip")).strip()
    level = int(args.get("level", 6) or 6)
    files_list = args.get("files")
    if isinstance(files_list, str):
        files_list = [f.strip() for f in files_list.split(",") if f.strip()]
    elif not isinstance(files_list, list):
        files_list = None

    res = archive_intel.archive_intel(
        op=op,
        path=path,
        target=target,
        files=files_list,
        archive_format=archive_format,
        level=level,
        allow=cfg.safety.allow_paths,
    )
    ok = not (res.startswith("archive not found:") or res.startswith("unknown archive_intel op") or res.startswith("security violation:") or '"error":' in res or '"status": "CORRUPT"' in res)
    return ActionResult(ok, res, needs_observe=False)


def _h_data_validate(args, obs, cfg):
    op = str(args.get("op", "validate")).strip()
    data = args.get("data")
    schema = args.get("schema")
    target = args.get("target")
    res = data_validate.data_validate(
        op=op,
        data=data,
        schema=schema,
        target=target,
        allow=cfg.safety.allow_paths,
    )
    ok = not (res.startswith("invalid JSON") or res.startswith("failed to read") or res.startswith("unknown data_validate op") or '"status": "INVALID"' in res)
    return ActionResult(ok, res, needs_observe=False)


def _h_crypto_intel(args, obs, cfg):
    op = str(args.get("op", "hash")).strip()
    target = str(args.get("target", "")).strip()
    algo = str(args.get("algo", "sha256")).strip()
    key = str(args.get("key", "")).strip()
    expected = str(args.get("expected", "")).strip()
    length = int(args.get("length", 32) or 32)
    salt = str(args.get("salt", "")).strip()
    iterations = int(args.get("iterations", 100000) or 100000)
    res = crypto_intel.crypto_intel(
        op=op,
        target=target,
        algo=algo,
        key=key,
        expected=expected,
        length=length,
        salt=salt,
        iterations=iterations,
        allow=cfg.safety.allow_paths,
    )
    ok = not (res.startswith("crypto_intel") or res.startswith("unknown crypto_intel op") or '"verified": false' in res)
    return ActionResult(ok, res, needs_observe=False)


def _h_diff_patch(args, obs, cfg):
    op = str(args.get("op", "diff")).strip()
    source = str(args.get("source", "")).strip()
    target = str(args.get("target", "")).strip()
    patch_text = str(args.get("patch_text", "")).strip()
    dry_run = bool(args.get("dry_run", False))
    res = diff_patch.diff_patch(
        op=op,
        source=source,
        target=target,
        patch_text=patch_text,
        dry_run=dry_run,
        allow=cfg.safety.allow_paths,
    )
    ok = not (res.startswith("target file not found:") or res.startswith("patch requires") or res.startswith("unknown diff_patch op"))
    return ActionResult(ok, res, needs_observe=False)


def _h_process_intel(args, obs, cfg):
    op = str(args.get("op", "list")).strip()
    pid = args.get("pid")
    try:
        pid = int(pid) if pid is not None and str(pid).strip() else None
    except (ValueError, TypeError):
        pid = None
    name = str(args.get("name", "")).strip()
    sort_by = str(args.get("sort_by", "memory")).strip()
    limit = int(args.get("limit", 20) or 20)
    force = bool(args.get("force", False))
    res = process_intel.process_intel(
        op=op,
        pid=pid,
        name=name,
        sort_by=sort_by,
        limit=limit,
        force=force,
        allow=cfg.safety.allow_paths,
    )
    ok = not (res.startswith("refused:") or res.startswith("Failed to list") or res.startswith("unknown process_intel op") or '"error":' in res)
    return ActionResult(ok, res, needs_observe=False)


def _h_regex_intel(args, obs, cfg):
    op = str(args.get("op", "extract")).strip()
    pattern = str(args.get("pattern", "")).strip()
    text = str(args.get("text", "")).strip()
    replacement = str(args.get("replacement", ""))
    preset = str(args.get("preset", "all")).strip()
    flags = str(args.get("flags", "")).strip()
    res = regex_intel.regex_intel(
        op=op,
        pattern=pattern,
        text=text,
        replacement=replacement,
        preset=preset,
        flags=flags,
        allow=cfg.safety.allow_paths,
    )
    ok = not (res.startswith("regex_intel op") or res.startswith("unknown regex_intel op") or '"error":' in res)
    return ActionResult(ok, res, needs_observe=False)


def _h_api_mock(args, obs, cfg):
    op = str(args.get("op", "start")).strip()
    port = int(args.get("port", 8999) or 8999)
    path = str(args.get("path", "/")).strip()
    method = str(args.get("method", "GET")).strip()
    status = int(args.get("status", 200) or 200)
    body = args.get("body", "")
    delay = float(args.get("delay", 0.0) or 0.0)
    res = api_mock.api_mock(
        op=op,
        port=port,
        path=path,
        method=method,
        status=status,
        body=body,
        delay=delay,
        allow=cfg.safety.allow_paths,
    )
    ok = not (res.startswith("unknown api_mock op") or '"status": "server_not_running"' in res or '"error":' in res)
    return ActionResult(ok, res, needs_observe=False)


def _h_cron_intel(args, obs, cfg):
    op = str(args.get("op", "explain")).strip()
    expr = str(args.get("expr", "* * * * *")).strip()
    count = int(args.get("count", 5) or 5)
    timezone_name = str(args.get("timezone_name", "UTC")).strip()
    base_time = str(args.get("base_time", "")).strip()
    res = cron_intel.cron_intel(
        op=op,
        expr=expr,
        count=count,
        timezone_name=timezone_name,
        base_time=base_time,
        allow=cfg.safety.allow_paths,
    )
    ok = not (res.startswith("unknown cron_intel op") or '"valid": false' in res or '"error":' in res)
    return ActionResult(ok, res, needs_observe=False)


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


def _h_system_diagnostics(args, obs, cfg):
    op = str(args.get("op", "health")).strip()
    auto_fix = bool(args.get("auto_fix", False))
    res = diagnostics.system_diagnostics(
        op=op,
        auto_fix=auto_fix,
        cfg=cfg,
        allow=cfg.safety.allow_paths,
    )
    return ActionResult(True, res, needs_observe=False)


def _h_net_intel(args, obs, cfg):
    op = str(args.get("op", "port_check")).strip()
    host = str(args.get("host", "127.0.0.1")).strip()
    port = int(args.get("port", 80) or 80)
    timeout = int(args.get("timeout", 3) or 3)
    res = net_intel.net_intel(
        op=op,
        host=host,
        port=port,
        timeout=timeout,
        allow=cfg.safety.allow_paths,
    )
    ok = not (res.startswith("failed to query") or res.startswith("unknown net_intel op") or '"status": "ERROR"' in res or '"status": "INVALID"' in res)
    return ActionResult(ok, res, needs_observe=False)


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
    entity = str(args.get("entity", "")).strip() or None
    relation = str(args.get("relation", "")).strip() or None
    target_entity = str(args.get("target_entity", "")).strip() or None
    from ..agent.memory import remember_fact
    msg = remember_fact(fact=fact, category=cat, entity=entity, relation=relation, target_entity=target_entity)
    return ActionResult(True, msg, needs_observe=False)


def _h_forget(args, obs, cfg):
    target = str(args.get("target", "")).strip()
    if not target:
        return ActionResult(False, "forget needs a 'target' parameter", needs_observe=False)
    from ..agent.memory import forget_fact
    msg = forget_fact(target=target)
    return ActionResult(True, msg, needs_observe=False)


def _h_memory_search(args, obs, cfg):
    query = str(args.get("query", "")).strip()
    if not query:
        return ActionResult(False, "memory_search needs a 'query' parameter", needs_observe=False)
    try:
        top_k = int(args.get("top_k", 5))
    except (TypeError, ValueError):
        top_k = 5
    from ..memory.manager import get_memory_manager
    mgr = get_memory_manager()
    results = mgr.search_semantic(query, top_k=top_k)
    if not results:
        return ActionResult(True, f"No relevant memories found for query '{query}'.", needs_observe=False)
    lines = [f"Found {len(results)} relevant memory item(s):"]
    for rec, score in results:
        lines.append(f"  • [{rec.category}] {rec.content} (similarity: {score:.2f})")
    return ActionResult(True, "\n".join(lines), needs_observe=False)


def _h_graph_query(args, obs, cfg):
    entity = str(args.get("entity", "")).strip()
    if not entity:
        return ActionResult(False, "graph_query needs an 'entity' parameter", needs_observe=False)
    from ..memory.manager import get_memory_manager
    mgr = get_memory_manager()
    subgraph = mgr.query_graph(entity)
    ent = subgraph.get("entity")
    relations = subgraph.get("relations", [])
    if not relations and not ent:
        return ActionResult(True, f"No Knowledge Graph connections found for entity '{entity}'.", needs_observe=False)
    lines = [f"Knowledge Graph Connections for '{entity}':"]
    for rel in relations:
        lines.append(f"  • {rel.source_name} --[{rel.relation_type}]--> {rel.target_name}" + (f" (context: {rel.context})" if rel.context else ""))
    return ActionResult(True, "\n".join(lines), needs_observe=False)


def _h_voice_control(args, obs, cfg):
    action = str(args.get("action", "status")).strip().lower()
    from ..utils import voice
    if action == "interrupt":
        was_speaking = voice.interrupt_speech()
        msg = "Interrupted active speech playback." if was_speaking else "Voice was not actively speaking."
        return ActionResult(True, msg, needs_observe=False)
    elif action == "enable_duplex":
        if cfg and cfg.voice:
            cfg.voice.full_duplex = True
        return ActionResult(True, "Full-duplex voice with real-time barge-in enabled.", needs_observe=False)
    elif action == "disable_duplex":
        if cfg and cfg.voice:
            cfg.voice.full_duplex = False
        return ActionResult(True, "Full-duplex voice disabled (half-duplex mode).", needs_observe=False)
    elif action == "set_sensitivity":
        val_str = str(args.get("value", "0.5")).strip()
        try:
            val = float(val_str)
            if cfg and cfg.voice:
                cfg.voice.barge_in_sensitivity = max(0.1, min(1.0, val))
            return ActionResult(True, f"Barge-in sensitivity set to {val}.", needs_observe=False)
        except ValueError:
            return ActionResult(False, f"Invalid sensitivity value '{val_str}'. Expected float 0.1-1.0.", needs_observe=False)
    else:  # status
        speaking = voice.is_speaking()
        duplex = getattr(cfg.voice, "full_duplex", True) if cfg else True
        sens = getattr(cfg.voice, "barge_in_sensitivity", 0.5) if cfg else 0.5
        msg = f"Voice Status: speaking={speaking}, full_duplex={duplex}, barge_in_sensitivity={sens}"
        return ActionResult(True, msg, needs_observe=False)


def _h_macro(args, obs, cfg):
    action = str(args.get("action", "list")).strip().lower()
    name = str(args.get("name", "")).strip()
    desc = str(args.get("description", "")).strip()
    speed = float(args.get("speed", 1.0))
    params = args.get("params") or {}

    from ..macro import get_macro_manager, MacroPlayer
    mgr = get_macro_manager()

    if action == "record":
        if not name:
            return ActionResult(False, "macro 'record' requires a 'name' parameter", needs_observe=False)
        from ..macro.recorder import get_macro_recorder
        rec = get_macro_recorder(mgr)
        rec.start_recording(name=name, description=desc)
        return ActionResult(True, f"Started recording macro '{name}'. Perform your actions on screen, then call macro(action='stop').", needs_observe=False)

    elif action == "stop":
        from ..macro.recorder import get_macro_recorder
        rec = get_macro_recorder(mgr)
        macro = rec.stop_recording(save_to_memory=True)
        return ActionResult(True, f"Saved macro '{macro.name}' ({len(macro.steps)} steps). Plan:\n\n{macro.format_plan()}", needs_observe=False)

    elif action == "play":
        if not name:
            return ActionResult(False, "macro 'play' requires a 'name' parameter", needs_observe=False)
        player = MacroPlayer(mgr)
        res = player.play(name, speed=speed, params=params)
        return ActionResult(res.get("ok", True), res.get("message", "Played."), needs_observe=True)

    elif action == "show":
        if not name:
            return ActionResult(False, "macro 'show' requires a 'name' parameter", needs_observe=False)
        macro = mgr.load_macro(name)
        if not macro:
            return ActionResult(False, f"Macro '{name}' not found.", needs_observe=False)
        return ActionResult(True, macro.format_plan(), needs_observe=False)

    elif action == "delete":
        if not name:
            return ActionResult(False, "macro 'delete' requires a 'name' parameter", needs_observe=False)
        ok = mgr.delete_macro(name)
        msg = f"Macro '{name}' deleted." if ok else f"Macro '{name}' could not be deleted."
        return ActionResult(ok, msg, needs_observe=False)

    else:  # list
        macros = mgr.list_macros()
        if not macros:
            return ActionResult(True, "No macros recorded yet. Use macro(action='record', name='...') to create one.", needs_observe=False)
        lines = [f"Found {len(macros)} saved macro(s):"]
        for m in macros:
            lines.append(f"  • {m.name} ({len(m.steps)} steps) - {m.description}")
        return ActionResult(True, "\n".join(lines), needs_observe=False)



def _h_skill(args, obs, cfg):
    """Search, load and write Jarvis Skills.

    Skills are instructions the model reads, so they are loaded in two stages:
    the prompt carries a one-line index and the body arrives only when the agent
    asks for a specific skill. Everything here is advisory - a skill can never
    grant a capability, and nothing in a body is executed.
    """
    action = str(args.get("action", "list")).strip().lower() or "list"
    name = str(args.get("name", "")).strip()
    query = str(args.get("query", "")).strip()

    from ..skills import Skill, get_skill_manager
    from ..skills.manager import MAX_BODY_CHARS, SkillError

    mgr = get_skill_manager()

    if action == "list":
        skills = mgr.list_skills()
        if not skills:
            return ActionResult(
                True,
                "No skills yet. Save one with skill(action='create', name=..., "
                "description=..., body=...) so the procedure survives the session.",
                needs_observe=False,
            )
        lines = [f"{len(skills)} skill(s) available:"]
        lines += [f"  • {skill.row()}" for skill in skills]
        lines.append("Load one with skill(action='load', name='...').")
        return ActionResult(True, "\n".join(lines), needs_observe=False)

    if action == "search":
        if not query:
            return ActionResult(False, "skill 'search' requires a 'query' parameter",
                                needs_observe=False)
        hits = mgr.search(query, limit=5)
        if not hits:
            return ActionResult(
                True,
                f"No skill matches {query!r}. Do the task with your own tools, then "
                f"save what worked: skill(action='create', ...).",
                needs_observe=False,
            )
        lines = [f"{len(hits)} skill(s) match {query!r}:"]
        lines += [f"  • {skill.row()} (match {score:.2f})" for score, skill in hits]
        lines.append("Load the best fit with skill(action='load', name='...').")
        return ActionResult(True, "\n".join(lines), needs_observe=False)

    if action in {"show", "load"}:
        if not name:
            return ActionResult(False, f"skill '{action}' requires a 'name' parameter",
                                needs_observe=False)
        skill = mgr.get(name)
        if skill is None:
            return ActionResult(
                False,
                f"No skill named {name!r}. Find one with "
                f"skill(action='search', query=...) or skill(action='list').",
                needs_observe=False,
            )
        shown = skill.render()
        if skill.rejected_tools:
            shown += (
                "\n\n(These tools named by the skill do not exist and were "
                f"ignored: {', '.join(skill.rejected_tools)})"
            )
        if action == "show":
            return ActionResult(True, shown, needs_observe=False)
        mgr.set_active(skill.name)
        return ActionResult(
            True,
            f"Skill '{skill.name}' is now active and its steps stay in context "
            f"while you work. Unload it with skill(action='unload') when the task "
            f"is done.\n\n{shown}",
            needs_observe=False,
        )

    if action == "unload":
        was = mgr.active()
        mgr.unload()
        return ActionResult(
            True,
            f"Skill '{was.name}' unloaded." if was else "No skill was loaded.",
            needs_observe=False,
        )

    if action in {"create", "update"}:
        if not name:
            return ActionResult(False, f"skill '{action}' requires a 'name' parameter",
                                needs_observe=False)
        body = str(args.get("body", "") or "")
        existing = mgr.get(name)
        if action == "create" and existing is not None:
            return ActionResult(
                False,
                f"A skill named '{existing.name}' already exists. Use "
                f"skill(action='update', name='{existing.name}', ...) to change it, "
                f"or pick a different name.",
                needs_observe=False,
            )
        if action == "update" and existing is None:
            return ActionResult(
                False,
                f"No skill named {name!r} to update. Use skill(action='create', ...).",
                needs_observe=False,
            )
        if not body.strip() and existing is None:
            return ActionResult(
                False,
                "a new skill needs a 'body' - the steps someone should follow. "
                "Write them as numbered markdown.",
                needs_observe=False,
            )
        if len(body) > MAX_BODY_CHARS:
            return ActionResult(
                False,
                f"that body is {len(body)} chars; the limit is {MAX_BODY_CHARS}. "
                f"Split it into two skills.",
                needs_observe=False,
            )

        declared = args.get("tools") or ""
        if isinstance(declared, (list, tuple)):
            tools = [str(t).strip() for t in declared]
        else:
            tools = [t.strip() for t in str(declared).split(",")]
        tools = [t for t in tools if t]

        skill = Skill(
            name=(existing.name if existing is not None else name),
            description=str(args.get("description", "") or "").strip()
            or (existing.description if existing is not None else ""),
            when_to_use=str(args.get("when_to_use", "") or "").strip()
            or (existing.when_to_use if existing is not None else ""),
            tools=tools or (existing.tools if existing is not None else []),
            body=body or (existing.body if existing is not None else ""),
            created=(existing.created if existing is not None else ""),
        )
        try:
            path = mgr.save(skill)
        except (SkillError, OSError) as exc:
            return ActionResult(False, f"could not save the skill: {exc}",
                                needs_observe=False)
        extra = ""
        if skill.rejected_tools:
            extra = (
                "\nIgnored these tool names because no such action exists: "
                f"{', '.join(skill.rejected_tools)}."
            )
        verb = "Updated" if action == "update" else "Saved"
        return ActionResult(
            True,
            f"{verb} skill '{skill.name}' ({path.name}). It is in the prompt index "
            f"from now on and loads with skill(action='load', name='{skill.name}')."
            f"{extra}",
            needs_observe=False,
        )

    if action == "delete":
        if not name:
            return ActionResult(False, "skill 'delete' requires a 'name' parameter",
                                needs_observe=False)
        ok = mgr.delete(name)
        return ActionResult(
            ok,
            f"Skill '{name}' deleted." if ok else f"No skill named {name!r} to delete.",
            needs_observe=False,
        )

    return ActionResult(
        False,
        f"unknown skill action '{action}'. Use one of: list, search, show, load, "
        f"unload, create, update, delete.",
        needs_observe=False,
    )


def _h_browser_action(args, obs, cfg):
    action = str(args.get("action", "snapshot")).strip().lower()
    from ..browser_engine import get_browser_driver
    driver = get_browser_driver(cfg=cfg)
    headless = args.get("headless")
    if headless is not None:
        try:
            headless = bool(headless)
        except Exception:
            headless = None

    shot_path = None
    if action in {"navigate", "goto", "open"}:
        url = str(args.get("url", "")).strip()
        if not url:
            return ActionResult(False, "browser_action 'navigate' requires a 'url' parameter", needs_observe=False)
        res = driver.navigate(url, headless=headless)
        shot_path = res.get("screenshot_path")
        msg = f"Navigated to {res.get('url')} ('{res.get('title')}').\n\n{res.get('snapshot', '')}"
        return ActionResult(res.get("ok", True), msg, needs_observe=False, image_path=shot_path)

    elif action in {"click"}:
        target = str(args.get("target", "")).strip()
        if not target:
            return ActionResult(False, "browser_action 'click' requires a 'target' (e.g. 'e1', CSS selector, or text)", needs_observe=False)
        res = driver.click(target)
        shot_path = res.get("screenshot_path")
        msg = f"{res.get('message', 'Clicked.')}\nPage: {res.get('url')} ('{res.get('title')}')\n\n{res.get('snapshot', '')}"
        return ActionResult(res.get("ok", True), msg, needs_observe=False, image_path=shot_path)

    elif action in {"type", "fill", "input"}:
        target = str(args.get("target", "")).strip()
        text = str(args.get("text", ""))
        press_enter = bool(args.get("press_enter", False))
        if not target:
            return ActionResult(False, "browser_action 'type' requires a 'target' (e.g. 'e1' or selector)", needs_observe=False)
        res = driver.type_text(target, text, press_enter=press_enter)
        shot_path = res.get("screenshot_path")
        msg = f"{res.get('message', 'Typed text.')}\nPage: {res.get('url')} ('{res.get('title')}')\n\n{res.get('snapshot', '')}"
        return ActionResult(res.get("ok", True), msg, needs_observe=False, image_path=shot_path)

    elif action in {"select"}:
        target = str(args.get("target", "")).strip()
        value = str(args.get("value", "")).strip()
        if not target or not value:
            return ActionResult(False, "browser_action 'select' requires both 'target' and 'value'", needs_observe=False)
        res = driver.select_option(target, value)
        shot_path = res.get("screenshot_path")
        return ActionResult(res.get("ok", True), f"{res.get('message')}\n\n{res.get('snapshot', '')}", needs_observe=False, image_path=shot_path)

    elif action in {"scroll"}:
        direction = str(args.get("direction", "down")).strip()
        amount = int(args.get("amount", 500) or 500)
        res = driver.scroll(direction=direction, amount=amount)
        shot_path = res.get("screenshot_path")
        return ActionResult(res.get("ok", True), f"{res.get('message')}\n\n{res.get('snapshot', '')}", needs_observe=False, image_path=shot_path)

    elif action in {"hover"}:
        target = str(args.get("target", "")).strip()
        res = driver.hover(target)
        shot_path = res.get("screenshot_path")
        return ActionResult(res.get("ok", True), f"{res.get('message')}\n\n{res.get('snapshot', '')}", needs_observe=False, image_path=shot_path)

    elif action in {"press"}:
        key = str(args.get("text", args.get("key", "Enter"))).strip()
        res = driver.press_key(key)
        shot_path = res.get("screenshot_path")
        return ActionResult(res.get("ok", True), f"{res.get('message')}\n\n{res.get('snapshot', '')}", needs_observe=False, image_path=shot_path)

    elif action in {"extract"}:
        target = str(args.get("target", "")).strip() or None
        mode = str(args.get("mode", "markdown")).strip()
        res = driver.extract_content(target=target, mode=mode)
        if not res.get("ok"):
            return ActionResult(False, res.get("message", "Extraction failed"), needs_observe=False)
        return ActionResult(True, f"Extracted content from {res.get('url')} ('{res.get('title')}'):\n\n{res.get('content')}", needs_observe=False)

    elif action in {"eval", "evaluate"}:
        script = str(args.get("text", args.get("script", ""))).strip()
        if not script:
            return ActionResult(False, "browser_action 'eval' requires a 'text' (JavaScript script)", needs_observe=False)
        res = driver.evaluate(script)
        if not res.get("ok"):
            return ActionResult(False, res.get("message", "Eval error"), needs_observe=False)
        return ActionResult(True, f"JavaScript Result: {res.get('result')}", needs_observe=False)

    elif action in {"snapshot", "inspect"}:
        snap = driver.snapshot()
        shot_path = snap.screenshot_path
        return ActionResult(True, snap.format_text(), needs_observe=False, image_path=shot_path)

    elif action in {"screenshot"}:
        path = str(args.get("path", "")).strip() or None
        shot_path = driver.take_screenshot(path=path)
        return ActionResult(True, f"Browser screenshot captured to: {shot_path}", needs_observe=False, image_path=shot_path)

    elif action in {"close"}:
        driver.close()
        return ActionResult(True, "Browser session closed.", needs_observe=False, clear_image=True)

    else:
        return ActionResult(False, f"Unknown browser_action '{action}'. Supported actions: navigate, click, type, select, scroll, hover, press, extract, snapshot, screenshot, eval, close.", needs_observe=False)


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
        ok, message, image_path = remote.send_task(cfg, device, task, timeout=timeout)
    except remote.RemoteError as exc:
        return ActionResult(False, str(exc), needs_observe=False)
    if image_path and not cfg.brain.use_vision:
        message += (" Vision is off, so the pixel preview cannot be inspected in this run; "
                    "the exact MOBILE UI ELEMENTS list is still available and must be used.")
    return ActionResult(ok, message, needs_observe=False, image_path=image_path,
                        clear_image=image_path is None)


def _h_wait(args, obs, cfg):
    secs = _num(args, "seconds", 1.0, 0.0, 10.0)
    time.sleep(secs)
    return ActionResult(True, f"waited {secs}s")


def _h_observe(args, obs, cfg):
    return ActionResult(True, "re-reading the screen", needs_observe=True)


def _h_finish(args, obs, cfg):
    return ActionResult(True, str(args.get("summary", "done")),
                        needs_observe=False, finished=True)


def _h_stop_session(args, obs, cfg):
    """Record that the agent wants this session to end.

    The handler only *asks*: the request lands in ``jarvis.session_control``,
    and the runtime that owns the session honours it (the console REPL breaks
    out, the browser worker tells its parent to shut the child down). Doing the
    shutdown here instead would mean killing the process from inside the agent
    loop, mid-step, with the current turn's output and trajectory unwritten.
    """
    from ..session_control import clean_reason, request_session_stop

    # Normalise before storing *and* before comparing, so "is this my reason?"
    # is asked of the same form the module keeps.
    reason = clean_reason(args.get("reason")) or "Stopping this session as requested."
    record = request_session_stop(reason=reason, source="agent")
    # A stop already in flight owns the reason the user will read; say so
    # rather than pretending this call started it.
    if record.get("reason") != reason:
        return ActionResult(
            True,
            f"This session is already stopping: {record.get('reason')}",
            needs_observe=False,
            stop_session=True,
        )
    return ActionResult(
        True,
        f"Session stop requested - closing down. Last note: {reason}",
        needs_observe=False,
        stop_session=True,
    )


def _h_set_theme(args, obs, cfg):
    theme = str(args.get("theme", "arc")).strip().lower()
    return ActionResult(True, f"UI visual theme set to '{theme}'", needs_observe=False)


def _h_secret(args, obs, cfg):
    from ..security import get_credential_vault
    vault = get_credential_vault()
    op = str(args.get("op", "list")).strip().lower()
    key = str(args.get("key", "")).strip()
    val = str(args.get("value", "")).strip()
    backend = str(args.get("backend", "credman")).strip().lower()

    if op == "list":
        secrets = vault.list_secrets()
        if not secrets:
            return ActionResult(True, "No credentials currently stored in vault.", needs_observe=False)
        lines = [f"- {s['key']} ({s['backend']}): {s['masked']}" for s in secrets]
        return ActionResult(True, f"Stored Credentials ({len(secrets)}):\n" + "\n".join(lines), needs_observe=False)

    elif op == "get":
        if not key:
            return ActionResult(False, "secret 'get' requires 'key'", needs_observe=False)
        secret_val = vault.get_secret(key)
        if not secret_val:
            return ActionResult(False, f"Secret '{key}' not found in Credential Vault.", needs_observe=False)
        masked = vault.mask_secret(secret_val)
        return ActionResult(True, f"Secret '{key}' exists in vault: {masked}", needs_observe=False)

    elif op == "set":
        if not key or not val:
            return ActionResult(False, "secret 'set' requires 'key' and 'value'", needs_observe=False)
        ok = vault.set_secret(key, val, backend=backend)
        if ok:
            masked = vault.mask_secret(val)
            return ActionResult(True, f"Securely stored '{key}' in Windows Credential Vault ({backend}): {masked}", needs_observe=False)
        return ActionResult(False, f"Failed to store '{key}' in Windows Credential Vault.", needs_observe=False)

    elif op == "delete":
        if not key:
            return ActionResult(False, "secret 'delete' requires 'key'", needs_observe=False)
        ok = vault.delete_secret(key)
        if ok:
            return ActionResult(True, f"Deleted secret '{key}' from Credential Vault.", needs_observe=False)
        return ActionResult(False, f"Secret '{key}' not found or could not be deleted.", needs_observe=False)

    elif op == "migrate":
        migrated = vault.migrate_from_env()
        if migrated:
            return ActionResult(True, f"Successfully migrated {len(migrated)} secret(s) to Windows Credential Manager: {', '.join(migrated)}", needs_observe=False)
        return ActionResult(True, "No unmanaged secrets found in .env or environment to migrate.", needs_observe=False)

    return ActionResult(False, f"Unknown secret op '{op}'. Must be one of: list, get, set, delete, migrate.", needs_observe=False)


def _h_see(args, obs, cfg):
    prompt = str(args.get("prompt", "What do you see?")).strip()
    source = str(args.get("source", "both")).strip().lower()
    camera = int(args.get("camera", 0))

    from ..perception import get_live_vision
    from ..agent.brain import make_brain

    vision = get_live_vision()
    brain = make_brain(cfg.brain)

    res = vision.analyze(source=source, prompt=prompt, brain=brain, camera_index=camera)
    return ActionResult(True, res, needs_observe=False)


def _h_self_heal(args, obs, cfg):
    strategy = str(args.get("strategy", "refocus")).strip().lower()
    target = str(args.get("target", "")).strip()

    from ..agent.tree_of_thought import SelfHealingDirector
    director = SelfHealingDirector(config_self_healing=True)

    if strategy == "refocus":
        target_win = target or (obs.active_window if obs else "")
        if not target_win:
            return ActionResult(False, "self_heal 'refocus' requires a target window title", needs_observe=False)
        ok = director.perform_refocus(target_win)
        if ok:
            return ActionResult(True, f"Self-healing: successfully restored and refocused window '{target_win}'.", needs_observe=True)
        return ActionResult(False, f"Self-healing: window matching '{target_win}' was not found.", needs_observe=False)

    elif strategy == "escape":
        ok = director.perform_escape()
        return ActionResult(True, "Self-healing: sent Escape key to clear dialogs/popups.", needs_observe=True)

    elif strategy == "restart_app":
        if not target:
            return ActionResult(False, "self_heal 'restart_app' requires a 'target' executable or app name", needs_observe=False)
        import subprocess
        exe_name = target if target.endswith(".exe") else f"{target}.exe"
        subprocess.run(["taskkill", "/F", "/IM", exe_name], capture_output=True, text=True)
        time.sleep(0.5)
        subprocess.Popen(target, shell=True)
        return ActionResult(True, f"Self-healing: killed and restarted application '{target}'.", needs_observe=True)

    elif strategy == "reset_state":
        try:
            import pyautogui  # type: ignore
            for key in ("ctrl", "alt", "shift", "win"):
                pyautogui.keyUp(key)
            return ActionResult(True, "Self-healing: released all stuck modifier keys (Ctrl, Alt, Shift, Win).", needs_observe=False)
        except Exception as exc:
            return ActionResult(False, f"Self-healing reset failed: {exc}", needs_observe=False)

    return ActionResult(False, f"Unknown self_heal strategy '{strategy}'. Supported: refocus, escape, restart_app, reset_state.", needs_observe=False)


def _h_daemon_rule(args, obs, cfg):
    action = str(args.get("action", "list")).strip().lower()
    from ..daemon import get_daemon, EventRule, EventType

    daemon = get_daemon(cfg=cfg)

    if action == "list":
        rules = daemon.list_rules()
        if not rules:
            return ActionResult(True, "No proactive daemon rules configured.", needs_observe=False)
        lines = ["Proactive Daemon Rules:"]
        for r in rules:
            st = "ENABLED" if r.enabled else "DISABLED"
            lines.append(f"  • [{r.id}] {r.name} ({st}) - Trigger: {r.trigger_type.value} -> {r.action_type.upper()}: '{r.action_target}' (cooldown: {int(r.cooldown_seconds)}s)")
        return ActionResult(True, "\n".join(lines), needs_observe=False)

    elif action == "status":
        rules = daemon.list_rules()
        active_cnt = sum(1 for r in rules if r.enabled)
        return ActionResult(True, f"Proactive Daemon Status: Active | {len(rules)} total rules ({active_cnt} enabled) | Watchers: Battery, Resource, File, Window, Routine.", needs_observe=False)

    elif action == "add":
        name = str(args.get("name", "Custom Rule")).strip()
        trigger_str = str(args.get("trigger", "custom")).strip().lower()
        action_type = str(args.get("action_type", "notify")).strip().lower()
        target = str(args.get("target", "")).strip()
        cooldown = float(args.get("cooldown", 300))

        if not target:
            return ActionResult(False, "daemon_rule 'add' requires 'target' (message text, task prompt, or macro name)", needs_observe=False)

        try:
            evt_type = EventType(trigger_str)
        except ValueError:
            evt_type = EventType.CUSTOM

        rule = EventRule(
            name=name,
            trigger_type=evt_type,
            action_type=action_type,
            action_target=target,
            cooldown_seconds=cooldown,
        )
        daemon.add_rule(rule)
        return ActionResult(True, f"Added proactive rule '{name}' [{rule.id}] for trigger '{evt_type.value}'.", needs_observe=False)

    elif action == "remove":
        rule_id = str(args.get("rule_id", "")).strip()
        if not rule_id:
            return ActionResult(False, "daemon_rule 'remove' requires 'rule_id'", needs_observe=False)
        ok = daemon.remove_rule(rule_id)
        if ok:
            return ActionResult(True, f"Removed proactive rule '{rule_id}'.", needs_observe=False)
        return ActionResult(False, f"Rule '{rule_id}' not found.", needs_observe=False)

    elif action in ("enable", "disable"):
        rule_id = str(args.get("rule_id", "")).strip()
        if not rule_id:
            return ActionResult(False, f"daemon_rule '{action}' requires 'rule_id'", needs_observe=False)
        en = action == "enable"
        ok = daemon.enable_rule(rule_id, enabled=en)
        if ok:
            return ActionResult(True, f"Rule '{rule_id}' is now {'enabled' if en else 'disabled'}.", needs_observe=False)
        return ActionResult(False, f"Rule '{rule_id}' not found.", needs_observe=False)

    return ActionResult(False, f"Unknown daemon_rule action '{action}'. Must be one of: list, status, add, remove, enable, disable.", needs_observe=False)


def _h_hud_control(args, obs, cfg):
    action = str(args.get("action", "status")).strip().lower()
    from ..hud import get_hud_controller
    controller = get_hud_controller(cfg=cfg)

    if action == "status":
        active = controller._is_active
        return ActionResult(True, f"Floating Mini HUD Status: {'ACTIVE' if active else 'INACTIVE'} | Hotkeys: {getattr(getattr(cfg, 'hud', None), 'hotkey_toggle', 'ctrl+alt+j')}", needs_observe=False)

    elif action == "show":
        controller.show_hud()
        return ActionResult(True, "Floating Mini HUD is now visible.", needs_observe=False)

    elif action == "hide":
        controller.hide_hud()
        return ActionResult(True, "Floating Mini HUD is now hidden.", needs_observe=False)

    elif action == "toggle":
        controller.toggle_hud()
        return ActionResult(True, "Floating Mini HUD visibility toggled.", needs_observe=False)

    elif action == "set_state":
        state_name = str(args.get("state", "idle")).strip().lower()
        detail = args.get("detail")
        controller.set_state(state_name, detail=str(detail) if detail else None)
        return ActionResult(True, f"HUD state updated to '{state_name}'.", needs_observe=False)

    return ActionResult(False, f"Unknown hud_control action '{action}'. Supported: show, hide, toggle, set_state, status.", needs_observe=False)


def _h_synthesize_tool(args, obs, cfg):
    name = str(args.get("name", "")).strip()
    description = str(args.get("description", "")).strip()
    code = str(args.get("code", "")).strip()
    parameters = args.get("parameters")
    tags = args.get("tags")
    test_args = args.get("test_args")
    mgr = tool_synthesis.get_tool_manager()
    ok, msg = mgr.synthesize(
        name=name,
        description=description,
        code=code,
        parameters=parameters,
        tags=tags,
        test_args=test_args,
    )
    return ActionResult(ok, msg, needs_observe=False)


def _h_execute_synthesized_tool(args, obs, cfg):
    name = str(args.get("name", "")).strip()
    tool_args = args.get("args") or {}
    timeout = args.get("timeout", 60)
    cwd = args.get("cwd")
    mgr = tool_synthesis.get_tool_manager()
    ok, msg = mgr.execute(name=name, args=tool_args, timeout=timeout, cwd=cwd)
    return ActionResult(ok, msg, needs_observe=False)


def _h_list_synthesized_tools(args, obs, cfg):
    query = args.get("query")
    mgr = tool_synthesis.get_tool_manager()
    tools = mgr.list_tools(filter_query=query)
    if not tools:
        return ActionResult(True, "No synthesized tools found.", needs_observe=False)
    lines = [f"Found {len(tools)} synthesized tool(s):"]
    for t in tools:
        lines.append(f"- Tool: '{t['name']}' (used {t['usage_count']}x): {t['description']}")
        if t.get("parameters"):
            import json as _json
            lines.append(f"  Parameters: {_json.dumps(t['parameters'])}")
        lines.append(f"  Path: {t['file_path']}")
    return ActionResult(True, "\n".join(lines), needs_observe=False)


def _h_ask(args, obs, cfg):
    q = str(args.get("question", "Could you clarify?"))
    return ActionResult(True, q, needs_observe=False, finished=True, ask=q)


# --------------------------------------------------------------------------- #
# action binding
# --------------------------------------------------------------------------- #
# ``schema.py`` owns each action's name, parameters and docs; this module owns
# the implementation. Naming a handler ``_h_<action>`` *is* the binding, so an
# action's name is written once instead of twice -- there is no second list to
# keep in step, and the two halves cannot drift apart unnoticed.
#
#   add an action     declare it in schema.py, define _h_<name> here
#   rename an action  rename the declaration and the handler
#   remove an action  delete both
#
# An action with no handler is absent from the table, so ``execute`` still
# raises UnknownAction for it exactly as it did when the table was hand-written;
# tests/test_action_space.py asserts both halves agree.


def _bind_handlers() -> dict[str, Any]:
    """Pair every declared action with its ``_h_<action>`` handler."""
    table: dict[str, Any] = {}
    unbound: list[str] = []

    for name in ACTIONS_BY_NAME:
        handler = globals().get(f"_h_{name}")
        if callable(handler):
            table[name] = handler
        else:
            unbound.append(name)

    orphaned = sorted(
        candidate[len("_h_"):]
        for candidate, value in globals().items()
        if candidate.startswith("_h_")
        and callable(value)
        and candidate[len("_h_"):] not in ACTIONS_BY_NAME
    )

    if unbound or orphaned:
        # Reported rather than raised: a mismatch must not change how Jarvis
        # runs, it must just be impossible to miss. tests/test_action_space.py
        # is the gate that keeps it from shipping.
        try:
            from ..utils import logging as log

            for name in unbound:
                log.warn(f"action '{name}' is declared in schema.py but has no _h_{name} handler")
            for name in orphaned:
                log.warn(f"handler _h_{name} has no action declared in schema.py")
        except Exception:  # pragma: no cover - reporting must never block import
            pass

    return table


_HANDLERS = _bind_handlers()


def handler_for(name: str) -> Any:
    """The handler bound to ``name``, or ``None`` when nothing is bound.

    The way in for callers and tests that need to ask what implements an action;
    the table itself stays private so the binding can change shape freely.
    """
    return _HANDLERS.get(name)
