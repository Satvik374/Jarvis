"""Screen control for the voice agent, resolved by description instead of pixels.

Why the main agent clicks accurately
------------------------------------
It never guesses a pixel. :mod:`jarvis.perception.elements` turns the desktop
into a numbered list of real UI Automation controls with their exact bounding
boxes, and :func:`jarvis.tools.registry._resolve_point` turns "element 4" back
into that control's exact centre. The model only has to answer *what*, and the
runtime supplies *where*.

Why the voice agent could not
-----------------------------
The direct-tool path executed actions with an **empty observation**
(``direct_tools.empty_observation()``), so ``click`` had nothing to resolve an
element id against. Passing raw ``x``/``y`` from a model that has never seen the
screen would be a guess - which is why the whole pointer family is excluded.

How this module closes the gap
------------------------------
From the other end. The voice model names its target in words and the runtime
resolves it against a *fresh* element list at execution time::

    voice: click_target("Save")
      -> fresh UIA read                     (~0.2s, exact bounding boxes)
      -> score every element's name and role
      -> one clear winner?  click THAT element's exact centre
      -> ambiguous?         refuse, and hand back the candidates to ask about
      -> nothing close?     refuse, and show the names that do exist

So the pixels are never guessed, and the two failure modes that make blind
clicking dangerous - an ambiguous name and a name that is not on screen - both
produce a question rather than a confident click on the wrong control.

When the tree cannot name the control
-------------------------------------
A control with no accessible name - an icon button, a canvas, a game surface,
an image - is invisible to UI Automation, so no wording can ever resolve
against it. On a failed resolution the tool takes a second, pixels-only read
and scores OCR text boxes the same way: still by name, still refusing when the
answer is ambiguous, and still clicking a resolved box's centre rather than a
guessed pixel. The element id is dispatched with the read it came from, because
ids only mean anything against their own element list.

What happens after the click
----------------------------
Every click re-reads the screen and reports what actually changed. Silence is
reported as silence: a click that left the screen identical is described that
way rather than as a success, because a return value cannot prove an effect.
A caller may pass ``retry`` to ask for one deliberate re-click, and that is
refused for toggle-shaped controls where a second activation would undo the
first.

What stays excluded
-------------------
Anything that still needs the caller to already know a pixel: raw ``click(x, y)``,
``move``, ``drag``, and bare ``type`` at "whatever happens to have focus". Those
are deliberately absent here. ``type_into`` exists instead, because focusing a
named control first is what makes typing deterministic.
"""

from __future__ import annotations

import difflib
import re
import threading
import time
from typing import Any

#: How long a single UIA read is reused. One action needs the same element list
#: for the click and for its report; a longer window would let a click resolve
#: against a screen the user has already changed.
_OBSERVATION_MAX_AGE = 1.5

#: Below this score, nothing on screen plausibly matches the description.
MIN_CONFIDENCE = 0.45

#: Scoring for the case where a control's name is only a *fragment* of the
#: request (``name in want``). Scaled by how much of the request it covers, and
#: calibrated so that a name covering less than half the request - one shared
#: word out of several - falls below MIN_CONFIDENCE and is refused rather than
#: clicked.
_FRAGMENT_FLOOR = 0.20
_FRAGMENT_RANGE = 0.70

#: When the runner-up is this close to the winner, the name is ambiguous and the
#: right answer is to ask - a coin-flip click is exactly the "confident but
#: wrong" failure that makes screen agents untrustworthy.
AMBIGUITY_MARGIN = 0.08

_MAX_LISTED = 60
_PUNCT = re.compile(r"[^\w\s]+")

#: How long to let the UI react before re-reading it to verify a click. Long
#: enough for a repaint or a dialog to appear, short enough that verifying the
#: click is not the reason a voice interaction feels slow.
_SETTLE_SECONDS = 0.25

#: A second activation would UNDO the first for these, so an automatic re-click
#: is never sent to one: a checkbox toggles back and a selection is re-issued.
#: The caller can still click them again deliberately.
_TOGGLE_ROLES = {
    "checkbox", "radiobutton", "menuitem", "tabitem", "listitem", "treeitem",
    "combobox", "slider",
}


def _normalize(text: Any) -> str:
    return " ".join(_PUNCT.sub(" ", str(text or "").lower()).split())


def _coverage(want: str, name: str) -> float:
    """How much of a control's label the request accounts for, 0..1.

    Guards the prefix/substring branches against a failure measured on a live
    screen: the word *prompt* matched inside a transcribed log line ("Voice AI
    Agent prompted Main Agent...") at 0.63 confidence, so ``click_target``
    would have clicked a log entry instead of an input. A one-word request
    buried in a long label is a coincidence, not a match.
    """
    if not want or not name:
        return 0.0
    # Tokens catch "prompt" inside a whole log sentence; characters separate
    # equally long labels, so "Save as" still outranks "Save changes".
    tokens = len(want.split()) / len(name.split())
    chars = len(want) / len(name)
    return min(1.0, 0.5 * tokens + 0.5 * chars)


# --------------------------------------------------------------------------
# Target resolution
# --------------------------------------------------------------------------

def score_element(element: Any, target: str, role: str = "") -> float:
    """Confidence in 0..1 that ``target`` names this control.

    Deliberately a plain, explainable ranker rather than anything learned: the
    failure has to be debuggable from the transcript, and every branch here maps
    to a sentence a person could have written ("it matched the label exactly",
    "it only shared the word *save*").
    """
    want = _normalize(target)
    name = _normalize(element.name)
    want_role = _normalize(role)
    element_role = _normalize(element.role)

    if not want and not want_role:
        return 0.0

    if not want:
        # Role-only query, e.g. "the search box" resolved as role=Edit.
        score = 0.5
    elif name == want:
        score = 1.0
    elif name.startswith(want):
        # A prefix is a strong signal, but only as far as it actually covers the
        # label: "save" for "Save As" is that button, while "open" for "open
        # the current document in a new editor window" is one shared word.
        score = 0.88 * (0.55 + 0.45 * _coverage(want, name))
    elif want in name:
        score = 0.78 * (0.45 + 0.55 * _coverage(want, name))
    elif name and name in want:
        # The control's name is a *fragment* of what was asked for, so the
        # request is more specific than anything on screen. Weight it by how
        # much of the request it actually accounts for. "Save" for "Save
        # document" is a real answer; "JARVIS" for "Fixing Jarvis Bugs" is a
        # coincidence of one shared word, and a flat score here would make it a
        # confident wrong click - found by measuring a live click, not in tests.
        coverage = len(name.split()) / max(1, len(want.split()))
        score = _FRAGMENT_FLOOR + _FRAGMENT_RANGE * coverage
    else:
        ratio = difflib.SequenceMatcher(None, want, name).ratio()
        tokens = set(want.split())
        overlap = len(tokens & set(name.split())) / len(tokens) if tokens else 0.0
        score = max(ratio * 0.62, overlap * 0.68)

    if want_role:
        if want_role == element_role:
            score += 0.18
        elif want_role in element_role or element_role in want_role:
            score += 0.09
        else:
            # A role the caller stated and this control is not: strong evidence
            # against, e.g. target="Save" role="button" must not hit a menu item.
            score -= 0.20

    score = max(0.0, min(1.0, score))

    # Applied AFTER the clamp, and as a penalty rather than a bonus. An exact
    # label match already saturates at 1.0, so a bonus would be lost there - and
    # an accessibility tree routinely carries a static label and a real control
    # with the same name (a Text "Save" beside the Save button). Without a
    # post-clamp adjustment that pair ties, and the tool refuses a click it could
    # have made correctly. The penalty exceeds AMBIGUITY_MARGIN on purpose: a
    # static label must lose outright, not merely nudge the score.
    if not getattr(element, "interactive", True):
        score *= 0.85

    return score


def rank(observation: Any, target: str, role: str = "") -> list[tuple[float, Any]]:
    """Every element scored against ``target``, best first (ties by id)."""
    scored = [
        (score_element(element, target, role), element)
        for element in getattr(observation, "elements", [])
    ]
    scored.sort(key=lambda pair: (-pair[0], pair[1].id))
    return scored


def _describe(element: Any) -> str:
    name = (element.name or "").strip().replace("\n", " ")[:50]
    label = f'"{name}"' if name else "(unlabeled)"
    cx, cy = element.center
    return f'[{element.id}] {element.role} {label} @ ({cx},{cy})'


def resolve(observation: Any, target: str, role: str = "") -> tuple[Any | None, str]:
    """Resolve a description to one control, or explain why it cannot.

    Returns ``(element, error)`` - exactly one is set. Every error string is
    written to be handed straight to a language model: it says what went wrong
    and what to do instead, because "not found" alone leaves it retrying blind.
    """
    ranked = rank(observation, target, role)
    if not ranked:
        window = getattr(observation, "active_window", "") or "the desktop"
        return None, (
            f"no readable UI Automation controls are on screen right now "
            f"(active window: {window}). Nothing can be clicked by name. Only "
            f"the foreground window can be read, so call focus_window to bring "
            f"the target app forward and look again; if that still shows nothing, "
            f"use execute_task, which can look at the screen."
        )

    best_score, best = ranked[0]
    if best_score < MIN_CONFIDENCE:
        closest = "; ".join(_describe(element) for _, element in ranked[:5])
        return None, (
            f"nothing on screen matches {target!r} closely enough "
            f"(best score {best_score:.2f} < {MIN_CONFIDENCE}). Closest controls: "
            f"{closest}. Either it is not visible, or the wording differs from "
            f"the on-screen label. Only the FOREGROUND window can be read, so "
            f"if the target is in another app call focus_window with its name "
            f"first. Otherwise call look_at_screen to read the real labels. Do "
            f"not guess a coordinate - you cannot see the screen, and a guessed "
            f"pixel clicks something else."
        )

    tied = [
        (score, element)
        for score, element in ranked[1:]
        if score >= best_score - AMBIGUITY_MARGIN
    ]
    if tied:
        options = "; ".join(
            _describe(element) for _, element in [(best_score, best), *tied]
        )
        return None, (
            f"{target!r} is ambiguous - {len(tied) + 1} controls match about as "
            f"well: {options}. Ask the user which one they mean (read the "
            f"options aloud) instead of picking one."
        )

    return best, ""


# --------------------------------------------------------------------------
# Live observation, briefly cached
# --------------------------------------------------------------------------

_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"observation": None, "at": 0.0}

#: The pixels-only read, cached on the same clock as the tree. ``False`` is the
#: tombstone for "read it, there was no text", so a screen with nothing to read
#: is not re-OCR'd on every failed resolution within the freshness window.
_OCR_CACHE: dict[str, Any] = {"observation": None, "at": 0.0}


def invalidate() -> None:
    """Drop the cached observations, so the next read sees the new screen."""
    with _CACHE_LOCK:
        _CACHE["observation"] = None
        _CACHE["at"] = 0.0
        _OCR_CACHE["observation"] = None
        _OCR_CACHE["at"] = 0.0


def _max_elements(cfg: Any) -> int:
    try:
        return max(1, int(cfg.perception.max_elements))
    except Exception:
        return _MAX_LISTED


def fresh_observation(cfg: Any = None, *, max_age: float = _OBSERVATION_MAX_AGE) -> Any:
    """Read the desktop's controls, reusing a very recent read when there is one."""
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE["observation"]
        if cached is not None and now - _CACHE["at"] <= max_age:
            return cached

    from ..perception import elements as elem_mod

    observation = elem_mod.observe(max_elements=_max_elements(cfg))
    with _CACHE_LOCK:
        _CACHE["observation"] = observation
        _CACHE["at"] = time.monotonic()
    return observation


def _active_window() -> str:
    try:
        from ..perception.elements import _active_window_title

        return _active_window_title()
    except Exception:
        return ""


def _execute(action: str, args: dict[str, Any], observation: Any, cfg: Any) -> Any:
    from ..tools import registry

    return registry.execute(action, args, observation, cfg)


# --------------------------------------------------------------------------
# Resolution, with a pixels-only second opinion
# --------------------------------------------------------------------------

def _ocr_observation(cfg: Any) -> Any:
    """A pixels-only read, for controls the accessibility tree cannot name.

    Deliberately its own function: this is the only part of resolution that
    touches OCR, so tests can stub it rather than depend on a live screen. The
    read is cached on the tree's freshness clock, because OCR costs roughly
    0.7s and one failing action would otherwise pay it twice.
    """
    try:
        if not bool(getattr(cfg.perception, "use_ocr", True)):
            return None
    except Exception:
        pass

    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _OCR_CACHE["observation"]
        if cached is not None and now - _OCR_CACHE["at"] <= _OBSERVATION_MAX_AGE:
            return cached or None

    try:
        from ..perception import elements as elem_mod

        observation = elem_mod.ocr_observation(max_elements=_max_elements(cfg))
    except Exception:
        observation = None

    with _CACHE_LOCK:
        _OCR_CACHE["observation"] = observation or False
        _OCR_CACHE["at"] = time.monotonic()
    return observation


def resolve_target(
    observation: Any, target: str, role: str = "", cfg: Any = None
) -> tuple[Any | None, str, Any]:
    """Resolve a spoken description, falling back to OCR on the pixels.

    Returns ``(element, error, observation)``. The returned observation is the
    one the element's id belongs to, and MUST be the one handed to the executor:
    ids only mean anything against the read they came from, so an OCR id pointed
    at a tree read would resolve to a different control entirely.
    """
    element, error = resolve(observation, target, role)
    if element is not None:
        return element, "", observation

    ocr_obs = _ocr_observation(cfg)
    if ocr_obs is not None:
        element, ocr_error = resolve(ocr_obs, target, role)
        if element is not None:
            return element, "", ocr_obs
        if "ambiguous" in ocr_error:
            # The pixels agree the name is on screen but not which control owns
            # it. That is a question to ask, which beats the tree's "nothing
            # matches" - and it names the right candidates.
            return None, ocr_error, observation
        # Otherwise keep the tree's error - it carries better recovery advice -
        # but say the pixels were checked too, so the model does not retry the
        # same wording expecting a different answer.
        error = (
            f"{error} The pixels were read with OCR as well and hold no closer "
            f"match, so this is not merely a naming difference."
        )
    return None, error, observation


def _fingerprint(observation: Any) -> tuple:
    """A cheap identity for the screen state, to diff before and after a click.

    Roles and names rather than ids: ids are positions in a list and shift when
    anything appears or disappears, so they would report a change on every read.
    """
    names = frozenset(
        (str(getattr(element, "role", "")), _normalize(getattr(element, "name", "")))
        for element in list(getattr(observation, "elements", []) or [])
    )
    return (str(getattr(observation, "active_window", "") or ""), names)


def _observe_change(cfg: Any, before_window: str, before_fp: tuple) -> tuple[str, bool]:
    """Re-read the screen after a click and describe what changed, if anything.

    Returns ``(note, changed)``. An unchanged screen is reported as unchanged:
    the runtime cannot prove a click took effect, so it must not imply it did.
    """
    time.sleep(_SETTLE_SECONDS)
    after_window = _active_window()
    if before_window and after_window and after_window != before_window:
        return f" Active window changed: {before_window!r} -> {after_window!r}.", True

    after = fresh_observation(cfg)
    if _fingerprint(after) != before_fp:
        return " The control list changed, so the click registered.", True
    return (
        " Nothing on screen has visibly changed, which is normal for a click that "
        "focuses a field or sets a toggle - but it is not proof the click took "
        "effect, so verify with look_at_screen before clicking again.",
        False,
    )


def _click_args(element: Any, count: int) -> dict[str, Any]:
    args: dict[str, Any] = {"element": element.id}
    if count != 1:
        args["count"] = count
    return args


def _retry_requested(args: dict[str, Any]) -> bool:
    """Only an explicit ``retry`` enables a second click.

    Default-off on purpose: the runtime cannot tell a click that did nothing from
    one whose effect is merely not visible yet (a slow page, an async submit),
    so re-clicking by default would risk activating a control twice.
    """
    value = args.get("retry")
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _reclick(cfg: Any, target: str, role: str, count: int,
            before_window: str, before_fp: tuple) -> str:
    """One deliberate re-click, resolved again against the new screen."""
    again = fresh_observation(cfg)
    element, _error, source = resolve_target(again, target, role, cfg)
    if element is None:
        return " It has left the screen since, so it was not clicked again."
    result = _execute("click", _click_args(element, count), source, cfg)
    invalidate()
    if not getattr(result, "ok", False):
        return f" The second click also failed: {result.message}"
    _note, changed = _observe_change(cfg, before_window, before_fp)
    if changed:
        return " Clicked it a second time and the screen responded."
    return (
        " Clicked it a second time and the screen still has not changed, so it is "
        "probably not the right control - ask the user rather than clicking again."
    )


# --------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------

def _h_look_at_screen(args: dict[str, Any], cfg: Any) -> dict[str, Any]:
    observation = fresh_observation(cfg)
    target = str(args.get("target") or "").strip()
    query = _normalize(args.get("query"))

    if target:
        element, error, observation = resolve_target(
            observation, target, str(args.get("role") or ""), cfg
        )
        if element is None:
            return {"ok": False, "error": error, "result": ""}
        cx, cy = element.center
        return {
            "ok": True,
            "result": (
                f"{target!r} resolves to {_describe(element)} - centre "
                f"({cx},{cy}). Nothing was clicked; this was a dry run. Use "
                f"click_target to act on it."
            ),
            "error": "",
        }

    elements = list(getattr(observation, "elements", []))
    if query:
        elements = [
            element
            for element in elements
            if query in _normalize(element.name) or query in _normalize(element.role)
        ]

    window = getattr(observation, "active_window", "") or "(unknown)"
    width, height = getattr(observation, "screen_size", (0, 0))
    if not elements:
        return {
            "ok": True,
            "result": (
                f"Active window: {window} ({width}x{height}). No controls matched "
                f"that filter. The desktop may have no accessible UI tree."
            ),
            "error": "",
        }

    listed = elements[:_MAX_LISTED]
    body = "\n".join(element.describe() for element in listed)
    more = f"\n(+{len(elements) - len(listed)} more)" if len(elements) > len(listed) else ""
    return {
        "ok": True,
        "result": (
            f"Active window: {window} ({width}x{height}). "
            f"{len(elements)} controls:\n{body}{more}\n"
            f"Act on one with click_target or type_into, naming it exactly as "
            f"written above."
        ),
        "error": "",
    }


def _h_click_target(args: dict[str, Any], cfg: Any) -> dict[str, Any]:
    target = str(args.get("target") or "").strip()
    if not target:
        return {"ok": False, "error": "click_target needs a 'target'", "result": ""}

    count = args.get("count")
    try:
        count = max(1, min(3, int(count))) if count is not None else 1
    except (TypeError, ValueError):
        count = 1

    role = str(args.get("role") or "")
    observation = fresh_observation(cfg)
    element, error, source = resolve_target(observation, target, role, cfg)
    if element is None:
        return {"ok": False, "error": error, "result": ""}

    before_window = _active_window()
    # Fingerprinted from the TREE read, never from ``source``: verification
    # re-reads the tree, and a pixels-only read has different roles and names,
    # so comparing the two would report a change on every single click.
    before_fp = _fingerprint(observation)
    result = _execute("click", _click_args(element, count), source, cfg)

    # The screen moved: anything resolved from here must be read again.
    invalidate()

    label = ("clicked" if count == 1 else f"clicked {count}x") + f" {_describe(element)}"
    if not getattr(result, "ok", False):
        return {
            "ok": False,
            "error": f"{label} but the click failed: {result.message}",
            "result": "",
        }

    cx, cy = element.center
    outcome, changed = _observe_change(cfg, before_window, before_fp)

    if not changed and _retry_requested(args):
        if str(getattr(element, "role", "")).lower() in _TOGGLE_ROLES:
            outcome += (
                " A second click was refused: this control toggles, so clicking it "
                "again would undo the first."
            )
        else:
            outcome += _reclick(cfg, target, role, count, before_window, before_fp)

    invalidate()

    origin = ""
    if source is not observation:
        origin = " (resolved from OCR text - the accessibility tree does not name it)"
    return {
        "ok": True,
        "result": (
            f"{label}{origin} - exact centre ({cx},{cy}) from the live element "
            f"list, not a guessed pixel.{outcome}"
        ),
        "error": "",
    }


def _h_type_into(args: dict[str, Any], cfg: Any) -> dict[str, Any]:
    target = str(args.get("target") or "").strip()
    text = str(args.get("text") or "")
    if not target:
        return {"ok": False, "error": "type_into needs a 'target'", "result": ""}
    if not text:
        return {"ok": False, "error": "type_into needs 'text'", "result": ""}

    observation = fresh_observation(cfg)
    element, error = resolve(observation, target, str(args.get("role") or ""))
    if element is None:
        return {"ok": False, "error": error, "result": ""}

    before = _active_window()
    focus = _execute("click", {"element": element.id}, observation, cfg)
    if not getattr(focus, "ok", False):
        return {
            "ok": False,
            "error": f"could not focus {_describe(element)}: {focus.message}",
            "result": "",
        }

    typed = _execute("type", {"text": text}, observation, cfg)
    if not getattr(typed, "ok", False):
        return {
            "ok": False,
            "error": f"focused {_describe(element)} but typing failed: {typed.message}",
            "result": "",
        }

    submitted = False
    if args.get("submit"):
        pressed = _execute("press", {"keys": "enter"}, observation, cfg)
        submitted = bool(getattr(pressed, "ok", False))

    invalidate()
    after = _active_window()
    summary = (
        f"typed {text!r} into {_describe(element)} "
        f"(focused at its exact centre, then typed at focus)"
    )
    if submitted:
        summary += " and pressed Enter"
    summary += "."
    if after and before and after != before:
        summary += f" Active window changed: {before!r} -> {after!r}."
    else:
        summary += (
            " The text cannot be read back from this control, so verify with "
            "look_at_screen if the result matters."
        )
    return {"ok": True, "result": summary, "error": ""}


def _h_press_keys(args: dict[str, Any], cfg: Any) -> dict[str, Any]:
    keys = str(args.get("keys") or "").strip()
    if not keys:
        return {"ok": False, "error": "press_keys needs 'keys'", "result": ""}

    observation = fresh_observation(cfg)
    target_window = _active_window()
    result = _execute("press", {"keys": keys}, observation, cfg)
    invalidate()
    if not getattr(result, "ok", False):
        return {"ok": False, "error": result.message, "result": ""}
    return {
        "ok": True,
        "result": (
            f"pressed {keys!r}; it went to the focused window, "
            f"which was {target_window or 'unknown'}."
        ),
        "error": "",
    }


def _h_scroll_window(args: dict[str, Any], cfg: Any) -> dict[str, Any]:
    def _clamped(key: str, default: int) -> int:
        raw = args.get(key)
        if raw is None:
            return default
        try:
            return max(-50, min(50, int(raw)))
        except (TypeError, ValueError):
            return default

    dy = _clamped("dy", 3)
    dx = _clamped("dx", 0)

    observation = fresh_observation(cfg)
    window = _active_window()
    result = _execute("scroll", {"dy": dy, "dx": dx}, observation, cfg)
    invalidate()
    if not getattr(result, "ok", False):
        return {"ok": False, "error": result.message, "result": ""}
    return {
        "ok": True,
        "result": (
            f"scrolled dy={dy} dx={dx} in {window or 'the focused window'}. "
            f"Call look_at_screen again before clicking - the list has moved."
        ),
        "error": "",
    }


# --------------------------------------------------------------------------
# Public surface
# --------------------------------------------------------------------------

_HANDLERS = {
    "look_at_screen": _h_look_at_screen,
    "click_target": _h_click_target,
    "type_into": _h_type_into,
    "press_keys": _h_press_keys,
    "scroll_window": _h_scroll_window,
}

#: Hand-written declarations, because these are voice-path helpers rather than
#: entries in :mod:`jarvis.tools.schema`. Arguments are structured
#: (``type``/``required``) so both live transports can derive their own wire
#: format from one definition: :func:`declarations` renders the Fish client-tool
#: shape, and ``jarvis.live.prompts`` renders the OpenAI Realtime JSON Schema.
#: ``direct_tools`` stamps on the shared ``tool_type`` / ``timeout_seconds``, so
#: the deadline still has exactly one definition.
DECLARATIONS: dict[str, dict[str, Any]] = {
    "look_at_screen": {
        "name": "look_at_screen",
        "description": (
            "Read the desktop as a numbered list of real UI Automation controls "
            "(role, exact label, exact centre pixel). Only the FOREGROUND window "
            "is readable - call focus_window first if the target is in another "
            "app. Pass 'target' to check what a description would resolve to "
            "WITHOUT clicking anything; that check also reads the pixels with "
            "OCR, so it answers for controls the accessibility tree cannot name. "
            "Use this whenever you are unsure a control is on screen, or of its "
            "exact wording."
        ),
        "arguments": [
            {
                "name": "target",
                "type": "str",
                "required": False,
                "description": (
                    "description to resolve as a dry run, e.g. 'Save' or "
                    "'the search box'"
                ),
            },
            {
                "name": "role",
                "type": "str",
                "required": False,
                "description": (
                    "control type to disambiguate, e.g. 'button', 'edit', "
                    "'menuitem'"
                ),
            },
            {
                "name": "query",
                "type": "str",
                "required": False,
                "description": "substring filter for the listing",
            },
        ],
    },
    "click_target": {
        "name": "click_target",
        "description": (
            "Click a control by name, resolved against the live element list so "
            "the click lands on its exact centre. If the accessibility tree "
            "cannot name the control, the pixels are read with OCR as a second "
            "opinion - so an icon or canvas control can still be clicked by "
            "describing its visible text. If the name is ambiguous or not on "
            "screen at all, nothing is clicked and you get the candidates to ask "
            "the user about. The screen is re-read afterwards and the result "
            "says whether anything actually changed. Open or focus the window "
            "first, then click."
        ),
        "arguments": [
            {
                "name": "target",
                "type": "str",
                "required": True,
                "description": "the control's on-screen label",
            },
            {
                "name": "role",
                "type": "str",
                "required": False,
                "description": "control type to disambiguate, e.g. 'button'",
            },
            {
                "name": "count",
                "type": "int",
                "required": False,
                "default": 1,
                "description": "clicks to send, 2 = double-click, 3 = triple",
            },
            {
                "name": "retry",
                "type": "bool",
                "required": False,
                "default": False,
                "description": (
                    "click once more if the screen did not change. Only for a "
                    "control that is safe to activate twice - never a toggle, a "
                    "checkbox or a button that submits something"
                ),
            },
        ],
    },
    "type_into": {
        "name": "type_into",
        "description": (
            "Focus a named text field and type into it. Always prefer this over "
            "bare typing: it resolves the field's exact centre first, so the "
            "text cannot land in the wrong window."
        ),
        "arguments": [
            {
                "name": "target",
                "type": "str",
                "required": True,
                "description": "the field's on-screen label",
            },
            {
                "name": "text",
                "type": "str",
                "required": True,
                "description": "the exact text to type",
            },
            {
                "name": "submit",
                "type": "bool",
                "required": False,
                "default": False,
                "description": "press Enter afterwards",
            },
            {
                "name": "role",
                "type": "str",
                "required": False,
                "description": "control type, e.g. 'edit'",
            },
        ],
    },
    "press_keys": {
        "name": "press_keys",
        "description": (
            "Press one key or a '+'-joined hotkey (e.g. 'enter', 'ctrl+s', "
            "'alt+tab') in the focused window. Report back which window received "
            "it before relying on the result."
        ),
        "arguments": [
            {
                "name": "keys",
                "type": "str",
                "required": True,
                "description": "key or '+'-joined combo",
            },
        ],
    },
    "scroll_window": {
        "name": "scroll_window",
        "description": (
            "Scroll the focused window. Positive dy scrolls down. Re-read the "
            "screen with look_at_screen before clicking after a scroll."
        ),
        "arguments": [
            {
                "name": "dy",
                "type": "int",
                "required": False,
                "default": 3,
                "description": "vertical clicks, positive = down",
            },
            {
                "name": "dx",
                "type": "int",
                "required": False,
                "default": 0,
                "description": "horizontal clicks, positive = right",
            },
        ],
    },
}

#: schema.py's parameter types, as the JSON Schema types both wire formats use.
_JSON_TYPES = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "dict": "object",
    "list": "array",
}


def _fish_argument(parameter: dict[str, Any]) -> dict[str, str]:
    """Render one argument in the Fish client-tool description convention.

    Matches :func:`jarvis.live.direct_tools._argument` so generated and
    hand-written declarations are indistinguishable to the model.
    """
    suffix = ""
    if not parameter.get("required"):
        default = parameter.get("default")
        suffix = (
            f" (optional, default {default!r})"
            if default is not None
            else " (optional)"
        )
    kind = _JSON_TYPES.get(parameter["type"], parameter["type"])
    return {
        "name": parameter["name"],
        "description": f"{kind}: {parameter['description']}{suffix}",
    }


def names() -> tuple[str, ...]:
    return tuple(DECLARATIONS)


def is_screen_tool(name: str) -> bool:
    return name in _HANDLERS


def declarations() -> list[dict[str, Any]]:
    """Client-tool declarations for the resolving screen tools."""
    return [
        {
            "name": declaration["name"],
            "description": declaration["description"],
            "arguments": [_fish_argument(arg) for arg in declaration["arguments"]],
        }
        for declaration in DECLARATIONS.values()
    ]


def parameters(name: str) -> list[dict[str, Any]]:
    """Structured argument specs, for transports that build their own schema."""
    declaration = DECLARATIONS.get(name)
    return list(declaration["arguments"]) if declaration else []


def run(name: str, args: dict[str, Any] | None = None, cfg: Any = None) -> dict[str, Any]:
    """Execute one screen tool. Never raises: the caller is a language model."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return {"ok": False, "error": f"unknown screen tool '{name}'", "result": ""}
    if args is not None and not isinstance(args, dict):
        return {"ok": False, "error": "'args' must be an object", "result": ""}

    try:
        if cfg is None:
            from ..config import load_config

            cfg = load_config()
        return handler(args or {}, cfg)
    except Exception as exc:  # a perception bug must not kill the voice session
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "result": ""}
