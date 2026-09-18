"""Direct action execution for the voice agent.

The main agent reaches actions through the perceive -> think -> act loop: it
screenshots the desktop, reads a numbered element list, picks one action, waits
for the result, and repeats.  That loop is what makes GUI work possible - and it
is also what makes a request like "what's my battery at?" or "open notepad" take
many seconds and several model round trips.

The voice agent does not need any of that.  Speech recognition and turn-taking
are already solved by the hosted agent, so when the user asks for something
deterministic the voice model can name the action itself and get the answer in
one hop:

    voice agent --client tool call--> browser page (app.js)
                                          |
                                          v
                              POST /api/tool/execute  (loopback, token-gated)
                                          |
                                          v
                              terminal runtime (this module)
                                          |
                                          v
                        jarvis.tools.registry.execute(name, args)

Nothing here is a new capability: every action is an existing entry in
:mod:`jarvis.tools.schema`, executed by the same registry the main agent uses.
Declarations are *generated* from that schema so argument names and shapes can
never drift apart from what the registry actually reads.

The one thing the schema cannot supply is a way to aim. A raw ``click`` needs a
pixel, and a voice model that has never seen the screen can only guess one - so
the pointer family stays excluded and :mod:`jarvis.live.screen_tools` provides
the resolving equivalents instead (name a control; the runtime reads the live
element list and finds its exact centre). Those six are appended to the
schema-derived set below and reach the model through the same path.

Why an exclusion list rather than a hand-written allowlist: "let the voice agent
do what the main agent can do" is the goal, so new actions become voice-callable
by default.  The exceptions below are the ones where that default is wrong, and
each is excluded for a reason that is about the *caller*, not about safety
theatre.
"""

from __future__ import annotations

from typing import Any

from ..tools.schema import ACTIONS_BY_NAME, Action

#: Actions the voice agent must never invoke directly, with the reason.
#:
#: ``screen``  - raw pointer and keyboard primitives cannot be aimed without
#:               the live element list, so the model would be guessing pixels.
#:               They must go through ``execute_task``, where the loop can see.
#:               The *resolving* screen tools in :mod:`jarvis.live.screen_tools`
#:               (name a control, the runtime finds its exact centre) are not
#:               excluded here - they are added below and solve the same problem
#:               from the other end.
#: ``loop``    - the agentic loop's own control flow, not a capability.
#: ``self``    - rewrites Jarvis's own source or permanently registers new code.
#: ``secret``  - reads and writes the credential vault.
#: ``session`` - long-lived state owned by another subsystem (the HUD, the
#:               proactive daemon, a recorder, an MCP server, the wake word),
#:               which the voice path must not mutate behind the main agent.
#: ``slow``    - routinely outruns the client-tool deadline; the voice agent
#:               delegates these to ``execute_task`` instead.
EXCLUDED: dict[str, str] = {
    # screen - raw primitives only; see live.screen_tools for the resolving
    # equivalents the voice agent is given instead (click_target, type_into,
    # press_keys, scroll_window, look_at_screen).
    "click": "screen",
    "double_click": "screen",
    "triple_click": "screen",
    "right_click": "screen",
    "move": "screen",
    "drag": "screen",
    "scroll": "screen",
    "type": "screen",
    "press": "screen",
    "key_sequence": "screen",
    "mouse_control": "screen",
    "observe": "screen",
    "see": "screen",
    "take_screenshot": "screen",
    "wait_for": "screen",
    # loop
    "finish": "loop",
    "ask": "loop",
    "wait": "loop",
    # self
    "self_upgrade": "self",
    "self_heal": "self",
    "synthesize_tool": "self",
    # secret
    "secret": "secret",
    # session
    "voice_control": "session",
    "remote_task": "session",
    "mcp": "session",
    "api_mock": "session",
    "macro": "session",
    "hud_control": "session",
    "daemon_rule": "session",
    # slow
    "code_task": "slow",
    "agent": "slow",
    "agent_swarm": "slow",
}

#: The client-tool deadline the voice agent is configured with, in seconds.
#: Fish caps a tool's server-side wait at 120s, so that is the hard ceiling.
TOOL_TIMEOUT_SECONDS = 120

#: Per-action ceiling for the arguments that let the model pick its own
#: timeout. A direct call that outlives the client-tool deadline does not just
#: fail - the model is told it failed while the command keeps running, which is
#: worse than a clean refusal.
_ARG_TIMEOUT_CLAMPS: dict[str, dict[str, int]] = {
    "run_command": {"timeout": 90},
    "python": {"timeout": 90},
    "session_exec": {"timeout": 90},
    "download_file": {},          # streamed; size-capped by max_mb
}

#: The dashboard-side wait, in milliseconds, passed to the browser SDK. Kept
#: just under the server deadline so the client gives up first and the agent
#: still hears a truthful result.
CLIENT_TOOL_TIMEOUT_MS = (TOOL_TIMEOUT_SECONDS - 10) * 1000

_MAX_DESCRIPTION = 1000


def names() -> tuple[str, ...]:
    """Every tool the voice agent may call directly: schema actions, then the
    resolving screen tools appended after them."""
    from . import screen_tools

    schema_names = tuple(name for name in ACTIONS_BY_NAME if name not in EXCLUDED)
    return schema_names + screen_tools.names()


def actions() -> tuple[Action, ...]:
    return tuple(ACTIONS_BY_NAME[name] for name in names() if name in ACTIONS_BY_NAME)


def is_direct(name: str) -> bool:
    from . import screen_tools

    if screen_tools.is_screen_tool(name):
        return True
    return name in ACTIONS_BY_NAME and name not in EXCLUDED


def _shorten(text: str, limit: int = _MAX_DESCRIPTION) -> str:
    """Trim a summary to ``limit`` chars on a word boundary."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:") + "."


def _argument(parameter) -> dict[str, str]:
    description = _shorten(parameter.description, 400)
    if not parameter.required:
        suffix = f" (optional, default {parameter.default!r})" \
            if parameter.default is not None else " (optional)"
    else:
        suffix = ""
    kind = {"str": "string", "int": "integer", "bool": "boolean",
            "float": "number", "dict": "object", "list": "array"}.get(
                parameter.type, parameter.type)
    return {
        "name": parameter.name,
        "description": f"{kind}: {description}{suffix}",
    }


def declarations() -> list[dict[str, Any]]:
    """Client-tool declarations for every directly callable action.

    Generated from :mod:`jarvis.tools.schema`, so an action's arguments here are
    exactly the arguments the registry reads.
    """
    out: list[dict[str, Any]] = []
    for action in actions():
        out.append(
            {
                "tool_type": "client",
                "name": action.name,
                "description": _shorten(action.summary),
                "arguments": [_argument(parameter) for parameter in action.params],
                "expects_response": True,
                "timeout_seconds": TOOL_TIMEOUT_SECONDS,
            }
        )
    out.extend(screen_declarations())
    return out


def screen_declarations() -> list[dict[str, Any]]:
    """Screen-tool declarations carrying the same transport fields.

    ``screen_tools`` hand-writes its declarations (they are voice-path helpers,
    not schema entries), so the shared ``tool_type`` / ``expects_response`` /
    ``timeout_seconds`` are stamped on here - one definition of the deadline
    instead of two that can drift.
    """
    from . import screen_tools

    out: list[dict[str, Any]] = []
    for declaration in screen_tools.declarations():
        entry = dict(declaration)
        entry.setdefault("tool_type", "client")
        entry["expects_response"] = True
        entry["timeout_seconds"] = TOOL_TIMEOUT_SECONDS
        out.append(entry)
    return out


def clamp_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Cap model-chosen timeouts so a direct call cannot outlive its deadline."""
    clamps = _ARG_TIMEOUT_CLAMPS.get(name)
    if not clamps or not isinstance(args, dict):
        return args
    out = dict(args)
    for key, ceiling in clamps.items():
        if key not in out:
            continue
        try:
            value = int(out[key])
        except (TypeError, ValueError):
            out.pop(key, None)
            continue
        out[key] = max(1, min(ceiling, value))
    return out


def empty_observation():
    """A minimal :class:`Observation` for actions that never read it.

    Every handler outside the pointer family ignores ``obs`` entirely, and the
    raw pointer family is excluded above - so this only has to exist, not be
    accurate. Screen size is still resolved when it is cheap to do so.

    The screen tools do NOT use this: they read a real, fresh element list (see
    :func:`jarvis.live.screen_tools.fresh_observation`), which is the whole
    reason they can aim.
    """
    from ..perception.elements import Observation

    size = (0, 0)
    try:
        from ..perception.screen import screen_size

        size = tuple(int(v) for v in screen_size())  # type: ignore[assignment]
    except Exception:
        pass
    return Observation(elements=[], screen_size=size)  # type: ignore[arg-type]


def _dispatch(
    name: str,
    args: dict[str, Any] | None = None,
    cfg: Any | None = None,
    obs: Any | None = None,
) -> dict[str, Any]:
    """Execute one action directly and return a JSON-ready result.

    Returns a dict rather than raising for expected failures, because the caller
    is a language model that has to be told what went wrong in words.
    """
    from . import screen_tools

    if screen_tools.is_screen_tool(name):
        # Resolving screen tools read the live element list, so they get their
        # own execution path - and their own fresh observation - instead of the
        # empty one the schema actions run against.
        if args is not None and not isinstance(args, dict):
            return {"ok": False, "error": "'args' must be an object", "result": ""}
        if cfg is None:
            from ..config import load_config

            cfg = load_config()
        result = screen_tools.run(name, args or {}, cfg)
        return {
            "ok": bool(result.get("ok")),
            "result": str(result.get("result", "")),
            "error": str(result.get("error", "")),
            "state": "running",
        }

    if name not in ACTIONS_BY_NAME:
        return {"ok": False, "error": f"unknown action '{name}'", "result": ""}
    if name in EXCLUDED:
        return {
            "ok": False,
            "error": (
                f"'{name}' is not callable as a direct tool ({EXCLUDED[name]}): "
                f"delegate it with execute_task instead"
            ),
            "result": "",
        }
    if args is not None and not isinstance(args, dict):
        return {"ok": False, "error": "'args' must be an object", "result": ""}

    from ..config import load_config
    from ..tools import registry

    if cfg is None:
        cfg = load_config()
    if obs is None:
        obs = empty_observation()

    try:
        result = registry.execute(name, clamp_args(name, args or {}), obs, cfg)
    except registry.UnknownAction:
        return {"ok": False, "error": f"unknown action '{name}'", "result": ""}
    except Exception as exc:  # a tool bug must not kill the voice session
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "result": ""}

    return {
        "ok": bool(result.ok),
        "result": str(result.message),
        "state": "finished" if result.finished else "running",
    }


#: A direct call is announced on one console line, so a voice action is visible
#: *while it happens* rather than only inferable from the cursor moving. Long
#: values (typed text, file content) are truncated to keep the stream readable.
_MAX_SHOWN_ARG = 60
_MAX_SHOWN_DETAIL = 160


def _oneline(text: Any) -> str:
    """Collapse a possibly multi-line tool message onto a single stream line."""
    flat = " ".join(str(text).split())
    if len(flat) > _MAX_SHOWN_DETAIL:
        flat = flat[: _MAX_SHOWN_DETAIL - 1] + "\u2026"
    return flat


def _fmt_call(name: str, args: dict[str, Any]) -> str:
    """Compact ``name(key=value)`` display, mirroring the main loop's format."""
    parts = []
    for key, value in (args or {}).items():
        shown = _oneline(value).replace("'", "\u2019")
        if len(shown) > _MAX_SHOWN_ARG:
            shown = shown[: _MAX_SHOWN_ARG - 1] + "\u2026"
        parts.append(f"{key}='{shown}'")
    return f"{name}({', '.join(parts)})"


def _announce(name: str, args: dict[str, Any], result: dict[str, Any]) -> None:
    """Log one direct action so the console and the browser UI can show it.

    Only the browser worker installs the activity bridge, so this is a plain
    console line in a normal CLI run - which is what is wanted there anyway.
    Logging must never be able to fail an action, hence the blanket guard.
    """
    try:
        from ..utils import logging as log

        call = _fmt_call(name, args)
        detail = _oneline(result.get("result") or result.get("error") or "")
        if result.get("ok"):
            log.ok(f"{call} -> {detail}" if detail else call)
        else:
            log.warn(f"{call} refused: {detail}" if detail else f"{call} refused")
    except Exception:
        pass


def run(
    name: str,
    args: dict[str, Any] | None = None,
    cfg: Any | None = None,
    obs: Any | None = None,
) -> dict[str, Any]:
    """Execute one direct action, announcing it to the activity stream.

    Thin wrapper over :func:`_dispatch`: the announcing is deliberately kept out
    of the dispatch logic so it can never change what an action actually does.
    """
    result = _dispatch(name, args, cfg, obs)
    _announce(name, args or {}, result)
    return result
