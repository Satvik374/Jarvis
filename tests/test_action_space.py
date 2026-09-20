"""The action space contract.

``schema.py`` declares every action (name, parameters, docs) and
``registry.py`` implements one ``_h_<action>`` handler per declaration, binding
them by name convention. This module is the gate on that pairing: adding an
action without a handler, or a handler without an action, fails here instead of
surfacing as an ``UnknownAction`` in the middle of a task.
"""

from __future__ import annotations

import pytest

from jarvis.tools import registry
from jarvis.tools.schema import ACTIONS, ACTIONS_BY_NAME, action_names, to_json_schema

KNOWN_PARAM_TYPES = {"bool", "dict", "float", "int", "list", "str"}
TERMINAL_ACTIONS = {"finish", "ask"}


def _declared_handlers() -> dict[str, object]:
    """Every ``_h_*`` callable defined in the registry module."""
    return {
        name: value
        for name, value in vars(registry).items()
        if name.startswith("_h_") and callable(value)
    }


# --------------------------------------------------------------------------- #
# the binding
# --------------------------------------------------------------------------- #

def test_every_declared_action_is_bound():
    unbound = [name for name in ACTIONS_BY_NAME if registry.handler_for(name) is None]
    assert unbound == [], f"declared actions with no handler: {unbound}"


def test_every_bound_name_is_declared():
    undeclared = sorted(set(registry._HANDLERS) - set(ACTIONS_BY_NAME))
    assert undeclared == [], f"dispatch entries with no declaration: {undeclared}"


def test_dispatch_table_matches_declarations_exactly():
    assert set(registry._HANDLERS) == set(ACTIONS_BY_NAME)


def test_no_handler_is_orphaned():
    declared = set(ACTIONS_BY_NAME)
    orphaned = sorted(set(_declared_handlers()) - {f"_h_{name}" for name in declared})
    assert orphaned == [], f"handlers with no declared action: {orphaned}"


def test_binding_follows_the_naming_convention():
    for name in ACTIONS_BY_NAME:
        assert registry.handler_for(name) is getattr(registry, f"_h_{name}")


def test_declaration_and_implementation_agree_on_count():
    assert len(registry._HANDLERS) == len(ACTIONS) == len(ACTIONS_BY_NAME)


# --------------------------------------------------------------------------- #
# the declared surface
# --------------------------------------------------------------------------- #

def test_action_names_are_unique():
    names = action_names()
    assert len(names) == len(set(names))


def test_actions_are_documented():
    empty = [a.name for a in ACTIONS if not a.summary.strip()]
    assert empty == [], f"actions without a summary: {empty}"


def test_actions_carry_examples():
    missing = [a.name for a in ACTIONS if not a.examples]
    assert missing == [], f"actions without an example: {missing}"


def test_parameters_are_fully_described():
    for action in ACTIONS:
        for param in action.params:
            assert param.name.strip(), f"{action.name}: parameter without a name"
            assert param.description.strip(), f"{action.name}.{param.name}: no description"
            assert param.type in KNOWN_PARAM_TYPES, (
                f"{action.name}.{param.name}: unknown type {param.type!r}"
            )


def test_parameter_names_are_unique_within_an_action():
    for action in ACTIONS:
        names = [p.name for p in action.params]
        assert len(names) == len(set(names)), f"{action.name}: duplicate parameter names"


def test_terminal_actions_are_the_only_loop_enders():
    terminal = {a.name for a in ACTIONS if a.terminal}
    assert terminal == TERMINAL_ACTIONS


# --------------------------------------------------------------------------- #
# the generated schema
# --------------------------------------------------------------------------- #

def test_json_schema_covers_every_action_once():
    entries = to_json_schema()
    assert [e["name"] for e in entries] == [a.name for a in ACTIONS]


def test_json_schema_required_matches_declared_required():
    by_name = {a.name: a for a in ACTIONS}
    for entry in to_json_schema():
        action = by_name[entry["name"]]
        expected = [p.name for p in action.params if p.required]
        assert entry["parameters"]["required"] == expected, entry["name"]
        assert set(entry["parameters"]["properties"]) == {p.name for p in action.params}, entry["name"]
        assert entry["description"] == action.summary


def test_json_schema_types_are_json_types():
    allowed = {"string", "integer", "number", "boolean", "array", "object"}
    for entry in to_json_schema():
        for prop in entry["parameters"]["properties"].values():
            assert prop["type"] in allowed, entry["name"]


# --------------------------------------------------------------------------- #
# the dispatch contract
# --------------------------------------------------------------------------- #

def test_execute_rejects_an_undeclared_name():
    with pytest.raises(registry.UnknownAction):
        registry.execute("definitely_not_a_real_action", {}, None, None)


def test_handler_for_reports_nothing_for_an_undeclared_name():
    assert registry.handler_for("definitely_not_a_real_action") is None


def test_execute_still_reports_unknown_when_an_action_is_unbound(monkeypatch):
    """An action with no handler stays a recoverable UnknownAction, not a crash.

    Callers already catch this (``jarvis.live.direct_tools`` maps it to a tool
    error), so it must keep raising the same way the hand-written table did.
    """
    assert "observe" in registry._HANDLERS
    monkeypatch.setattr(
        registry, "_HANDLERS", {k: v for k, v in registry._HANDLERS.items() if k != "observe"}
    )
    with pytest.raises(registry.UnknownAction):
        registry.execute("observe", {}, None, None)


def test_binding_is_recomputable_from_the_declarations():
    """The table is a function of the declarations, not a second list to maintain."""
    assert registry._bind_handlers() == registry._HANDLERS
