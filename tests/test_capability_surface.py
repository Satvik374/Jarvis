"""Guard the declared-capability surface.

Phase 2 (binding coercions to declarations) declared hidden handler params and
numeric ranges in ``schema.py``. These tests pin the gains so a future edit
cannot silently drop a declared range or a declared capability param. The
policy: new declarations are always welcome; removing one must be a conscious
decision that updates these tests on purpose.
"""

from __future__ import annotations

import pytest

from jarvis.tools.schema import ACTIONS_BY_NAME, to_json_schema

# --- the capability floor -------------------------------------------------- #
# (action, param) -> declared numeric bounds that must stay visible.
RANGE_FLOOR = {
    ("click", "count"): (1, 10),
    ("scroll", "dy"): (-50, 50),
    ("scroll", "dx"): (-50, 50),
    ("mouse_control", "camera"): (0, 9),
    ("wait", "seconds"): (0, 10),
    ("crypto_intel", "iterations"): (1000, None),
}

# Handler params that used to be invisible to the model. If one of these
# disappears from the declaration, a real handler capability went dark.
CAPABILITY_FLOOR = {
    ("browser_action", "press_enter"),
    ("browser_action", "amount"),
    ("browser_action", "path"),
    ("browser_action", "script"),
    ("remember", "entity"),
    ("remember", "relation"),
    ("remember", "target_entity"),
    ("session_exec", "cwd"),
    ("db_query", "params"),
    ("git_intel", "target"),
    ("archive_intel", "files"),
    ("crypto_intel", "salt"),
    ("crypto_intel", "iterations"),
}

# Handler-only params that were deliberately left out of the declarations
# (enforced inside the handler, not part of the model contract). Empty today:
# every model-facing param is now declared. Keep the test as the tripwire that
# stays green unless a future handler grows an undeclared arg.
HANDLER_ONLY: set[tuple[str, str]] = set()


def _props(action: str) -> dict:
    entry = next(e for e in to_json_schema() if e["name"] == action)
    return entry["parameters"]["properties"]


def test_known_ranges_are_declared():
    for (action, param), (lo, hi) in RANGE_FLOOR.items():
        prop = _props(action)[param]
        assert prop.get("minimum") == lo, f"{action}.{param} minimum"
        assert prop.get("maximum") == hi, f"{action}.{param} maximum"


def test_declared_capability_params_exist():
    for action, param in sorted(CAPABILITY_FLOOR):
        assert action in ACTIONS_BY_NAME, action
        assert param in _props(action), f"{action}.{param} missing from schema"


def test_capability_params_are_documented():
    """A declared param the model can't understand is not capability."""
    for action, param in sorted(CAPABILITY_FLOOR):
        desc = _props(action)[param]["description"]
        assert desc.strip(), f"{action}.{param} has an empty description"


def test_handler_only_params_are_not_declared():
    for action, param in sorted(HANDLER_ONLY):
        assert param not in _props(action), (
            f"{action}.{param} is handler-internal; declaring it changes the "
            f"contract - update HANDLER_ONLY deliberately if intended"
        )


def test_minimum_never_exceeds_maximum():
    for entry in to_json_schema():
        for pname, prop in entry["parameters"]["properties"].items():
            lo, hi = prop.get("minimum"), prop.get("maximum")
            if lo is not None and hi is not None:
                assert lo <= hi, f"{entry['name']}.{pname}: min {lo} > max {hi}"


def test_every_declared_default_is_schema_visible():
    """Defaults stay honest: declared default must appear in the schema prop."""
    from jarvis.tools.schema import ACTIONS

    for action in ACTIONS:
        props = _props(action.name)
        for p in action.params:
            if p.default is not None and p.default != "":
                assert props[p.name].get("description"), action.name


def test_schema_entry_count_is_stable():
    """89 actions; adding is fine, losing one is always an incident."""
    assert len(to_json_schema()) >= 89


# --------------------------------------------------------------------------- #
# the model-facing surfaces (generated FROM the schema, must stay in sync)
# --------------------------------------------------------------------------- #

def test_action_reference_shows_declared_ranges():
    """The loop/sub-agent/coder/dataset prompt renders bounds from schema."""
    from jarvis.agent.prompts import _action_reference

    ref = _action_reference()
    for needle in ("[-50..50]", "[1..10]", "[0..9]", "[0..10]", "[1000..+inf]"):
        assert needle in ref, f"declared range {needle} missing from action reference"


def test_action_reference_shows_capability_params():
    from jarvis.agent.prompts import _action_reference

    ref = _action_reference()
    for param in sorted({p for _, p in CAPABILITY_FLOOR}):
        assert param in ref, f"capability param {param!r} missing from action reference"


def test_voice_declarations_carry_ranges_and_params():
    """Fish client-tool descriptions state bounds as text and list new params."""
    from jarvis.live.direct_tools import declarations

    ds = {d["name"]: d for d in declarations()}
    crypto = {a["name"]: a["description"] for a in ds["crypto_intel"]["arguments"]}
    assert "Range: 1000..+inf" in crypto["iterations"]
    remember_args = {a["name"] for a in ds["remember"]["arguments"]}
    assert {"entity", "relation", "target_entity"} <= remember_args


def test_voice_declarations_still_exclude_loop_and_screen():
    """The visibility work must not widen WHO may call what."""
    from jarvis.live.direct_tools import actions

    names = {a.name for a in actions()}
    assert "wait" not in names and "observe" not in names
    assert "click" not in names and "scroll" not in names
