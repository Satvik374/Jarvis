"""Tests for the voice agent's screen control.

The claim these tests exist to defend: the voice agent can click the user's
screen *accurately* without ever seeing it. That is only true if a click is
dispatched as an element id resolved against a live UI Automation read, so the
runtime - not the model - supplies the pixel.

The failure modes that would make screen control dangerous are pinned here too:
an ambiguous name and a missing name must both produce a question, never a
confident click on the wrong control.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jarvis.live import direct_tools, screen_tools
from jarvis.perception.elements import Element, Observation

# -------------------------------------------------------------------------- #
# fixtures
# -------------------------------------------------------------------------- #

def make_element(element_id: int, role: str, name: str,
                 bbox: tuple[int, int, int, int] = (0, 0, 20, 20),
                 interactive: bool = True) -> Element:
    """An element whose centre is the true midpoint of its bounding box.

    Built the way ``_detect_uia`` builds them, so 'exact centre' in a test means
    the same pixel the main agent would click.
    """
    left, top, right, bottom = bbox
    return Element(
        id=element_id,
        role=role,
        name=name,
        bbox=bbox,
        center=((left + right) // 2, (top + bottom) // 2),
        interactive=interactive,
    )


def make_observation(*elements: Element, active_window: str = "Notepad") -> Observation:
    return Observation(
        elements=list(elements),
        screen_size=(1920, 1080),
        active_window=active_window,
    )


@pytest.fixture(autouse=True)
def _clean_cache():
    screen_tools.invalidate()
    yield
    screen_tools.invalidate()


@pytest.fixture
def cfg():
    """A config whose perception cap is the real default."""
    settings = MagicMock()
    settings.perception.max_elements = 60
    return settings


def run_tool(name: str, args: dict | None = None, obs: Observation | None = None,
             cfg: MagicMock | None = None) -> dict:
    """Run a screen tool against a fixed observation, capturing dispatches."""
    if obs is None:
        obs = make_observation()
    if cfg is None:
        cfg = MagicMock()
    with patch.object(screen_tools, "fresh_observation", return_value=obs), \
            patch.object(screen_tools, "_active_window", return_value="Notepad"), \
            patch("jarvis.tools.registry.execute") as execute:
        execute.return_value = MagicMock(ok=True, message="did it")
        result = screen_tools.run(name, args, cfg)
    result["_dispatches"] = execute.call_args_list
    return result


def dispatched(result: dict) -> list[tuple[str, dict]]:
    return [(call.args[0], call.args[1]) for call in result["_dispatches"]]


# -------------------------------------------------------------------------- #
# precision: the runtime supplies the pixel, never the model
# -------------------------------------------------------------------------- #

def test_click_dispatches_an_element_id_and_no_coordinates():
    """The whole accuracy argument in one assertion.

    The model names a control; the runtime resolves its exact centre. If a click
    ever carried x/y from the model, accuracy would be a guess again.
    """
    button = make_element(3, "Button", "Save", bbox=(100, 200, 180, 240))
    result = run_tool("click_target", {"target": "Save"},
                      obs=make_observation(button))

    assert result["ok"] is True
    assert dispatched(result) == [("click", {"element": 3})]
    action, args = dispatched(result)[0]
    assert "x" not in args and "y" not in args
    # The element's centre is the true bounding-box midpoint: (140, 220).
    assert button.center == (140, 220)
    assert "(140,220)" in result["result"]


def test_click_reports_the_exact_point_it_used():
    button = make_element(1, "Button", "OK", bbox=(10, 20, 30, 40))
    result = run_tool("click_target", {"target": "OK"}, obs=make_observation(button))
    assert "(20,30)" in result["result"]


def test_click_resolves_by_id_never_by_a_stale_index():
    """Element ids come from the current read, and the id is what is sent.

    A click that sent a *position* in the list would silently hit the wrong
    control as soon as the list changed.
    """
    elements = [
        make_element(7, "Button", "Cancel"),
        make_element(9, "Button", "Publish"),
        make_element(11, "Button", "Save"),
    ]
    result = run_tool("click_target", {"target": "Publish"},
                      obs=make_observation(*elements))
    assert dispatched(result) == [("click", {"element": 9})]


def test_click_count_is_capped_like_the_registry_expects():
    button = make_element(1, "ListItem", "Report.txt")
    assert dispatched(run_tool("click_target", {"target": "Report.txt", "count": 2},
                               obs=make_observation(button))) == [
        ("click", {"element": 1, "count": 2})
    ]
    # Absurd values are clamped into the registry's own 1..10 range.
    assert dispatched(run_tool("click_target", {"target": "Report.txt", "count": 99},
                               obs=make_observation(button))) == [
        ("click", {"element": 1, "count": 3})
    ]
    assert dispatched(run_tool("click_target", {"target": "Report.txt", "count": 0},
                               obs=make_observation(button))) == [
        ("click", {"element": 1})
    ]


# -------------------------------------------------------------------------- #
# scoring
# -------------------------------------------------------------------------- #

def test_exact_match_beats_prefix_beats_substring():
    elements = [
        make_element(1, "Button", "Save changes"),
        make_element(2, "Button", "Save as"),
        make_element(3, "Button", "Save"),
    ]
    obs = make_observation(*elements)
    scores = {element.name: score for score, element in screen_tools.rank(obs, "Save")}
    assert scores["Save"] > scores["Save as"] > scores["Save changes"]
    assert screen_tools.resolve(obs, "Save")[0].id == 3


def test_a_more_specific_name_wins_over_its_own_prefix():
    elements = [
        make_element(1, "Button", "Save"),
        make_element(2, "Button", "Save as"),
    ]
    obs = make_observation(*elements)
    assert screen_tools.resolve(obs, "Save as")[0].id == 2


def test_role_separates_two_controls_with_the_same_label():
    """Two 'Save' controls in one window is exactly when a blind click goes wrong."""
    save_button = make_element(1, "Button", "Save")
    save_menu = make_element(2, "MenuItem", "Save")
    obs = make_observation(save_button, save_menu)

    # Without a role these are indistinguishable, so the tool must refuse.
    assert screen_tools.resolve(obs, "Save")[0] is None
    # With the role, the answer is unambiguous.
    resolved, error = screen_tools.resolve(obs, "Save", role="button")
    assert error == ""
    assert resolved.id == 1


def test_a_stated_role_rules_out_a_control_of_another_kind():
    radio = make_element(1, "RadioButton", "Dark mode")
    obs = make_observation(radio)
    # The label matches exactly, but the caller asked for something else.
    element, error = screen_tools.resolve(obs, "Dark mode", role="button")
    assert element is not None  # the label is still the best available answer
    assert screen_tools.score_element(radio, "Dark mode", role="edit") < \
        screen_tools.score_element(radio, "Dark mode")


def test_real_controls_are_preferred_over_static_text():
    """A label and a control sharing a name must resolve, not read as ambiguous."""
    label = make_element(1, "Text", "Settings", interactive=False)
    button = make_element(2, "Button", "Settings", interactive=True)
    obs = make_observation(label, button)

    assert screen_tools.score_element(button, "Settings") > \
        screen_tools.score_element(label, "Settings")
    # The gap has to clear the ambiguity margin, or the tool still refuses.
    assert (screen_tools.score_element(button, "Settings")
            - screen_tools.score_element(label, "Settings")) > \
        screen_tools.AMBIGUITY_MARGIN
    assert screen_tools.resolve(obs, "Settings")[0].id == 2


def test_the_control_preference_survives_an_exact_match_saturating():
    """Regression: a preference added before the clamp is lost at score 1.0.

    An exact label match clamps to 1.0, so the only way this preference can ever
    have an effect is by adjusting the score *after* the clamp - which is why it
    is a penalty here rather than a bonus.
    """
    exact = make_element(1, "Button", "Save")
    assert screen_tools.score_element(exact, "Save") == 1.0
    static_twin = make_element(2, "Text", "Save", interactive=False)
    assert screen_tools.score_element(static_twin, "Save") < 1.0


def test_fuzzy_match_accepts_a_natural_paraphrase():
    button = make_element(1, "Button", "Save")
    assert screen_tools.score_element(button, "Save document") >= screen_tools.MIN_CONFIDENCE


def test_a_control_named_for_one_shared_word_does_not_win():
    """Regression, found by measuring a live click rather than in a test.

    Asked for 'Fixing Jarvis Bugs' the tool resolved a control named 'JARVIS'
    and clicked it, because a name that is a fragment of the request scored a
    flat 0.72. That is a confident wrong click; the control's name has to account
    for most of the request before it counts.
    """
    stray = make_element(1, "Text", "JARVIS")
    score = screen_tools.score_element(stray, "Fixing Jarvis Bugs")
    assert score < screen_tools.MIN_CONFIDENCE
    assert screen_tools.resolve(make_observation(stray), "Fixing Jarvis Bugs")[0] is None


def test_a_fragment_score_rises_with_how_much_of_the_request_it_covers():
    mostly = make_element(1, "Button", "Save")
    barely = make_element(2, "Button", "Save")
    covered = screen_tools.score_element(mostly, "Save document")
    strayed = screen_tools.score_element(barely, "Save this document to disk")
    assert covered > strayed
    assert covered >= screen_tools.MIN_CONFIDENCE


# -------------------------------------------------------------------------- #
# refusal: a wrong click is worse than no click
# -------------------------------------------------------------------------- #

def test_ambiguous_name_clicks_nothing_and_names_the_candidates():
    elements = [
        make_element(1, "Button", "Save", bbox=(0, 0, 40, 20)),
        make_element(2, "Button", "Save", bbox=(0, 40, 40, 60)),
    ]
    result = run_tool("click_target", {"target": "Save"},
                      obs=make_observation(*elements, active_window="Editor"))

    assert result["ok"] is False
    assert dispatched(result) == []
    assert "ambiguous" in result["error"]
    # Both candidates, with their exact centres, so the model can ask usefully.
    assert "(20,10)" in result["error"] and "(20,50)" in result["error"]
    assert "ask the user" in result["error"].lower()


def test_missing_name_clicks_nothing_and_lists_what_is_there():
    elements = [
        make_element(1, "Button", "Cancel"),
        make_element(2, "Button", "Help"),
    ]
    result = run_tool("click_target", {"target": "Publish"},
                      obs=make_observation(*elements))

    assert result["ok"] is False
    assert dispatched(result) == []
    assert "nothing on screen matches" in result["error"]
    assert "Cancel" in result["error"] and "Help" in result["error"]
    # It must tell the model what to do next, not just that it failed.
    assert "look_at_screen" in result["error"]


def test_a_missing_control_points_at_the_foreground_window_as_the_likely_cause():
    """Only the foreground window is readable, and that is the usual reason.

    Verified on the real desktop: launching Notepad while the browser kept focus
    left Notepad's controls entirely absent from the list. A refusal that does
    not say this sends the model round the same loop again.
    """
    result = run_tool("click_target", {"target": "Publish"},
                      obs=make_observation(make_element(1, "Button", "Help")))
    assert result["ok"] is False
    assert "focus_window" in result["error"]
    assert "FOREGROUND" in result["error"]


def test_an_empty_read_points_at_the_foreground_window_too():
    result = run_tool("click_target", {"target": "Save"}, obs=make_observation())
    assert "focus_window" in result["error"]


def test_the_listing_tool_says_only_the_foreground_window_is_readable():
    declaration = next(d for d in screen_tools.declarations()
                       if d["name"] == "look_at_screen")
    assert "FOREGROUND" in declaration["description"]
    assert "focus_window" in declaration["description"]


def test_empty_screen_returns_guidance_not_a_crash():
    result = run_tool("click_target", {"target": "Save"}, obs=make_observation())
    assert result["ok"] is False
    assert result["_dispatches"] == []
    assert "execute_task" in result["error"]


def test_unlabeled_control_is_shown_as_unlabeled():
    elements = [make_element(1, "Button", "")]
    result = run_tool("click_target", {"target": "Save"},
                      obs=make_observation(*elements))
    assert "(unlabeled)" in result["error"]


def test_a_missing_target_argument_is_refused():
    for tool in ("click_target", "type_into"):
        result = run_tool(tool, {"text": "hi"})
        assert result["ok"] is False
        assert "needs a 'target'" in result["error"]
        assert dispatched(result) == []


def test_type_into_needs_text():
    result = run_tool("type_into", {"target": "Search"})
    assert result["ok"] is False
    assert "needs 'text'" in result["error"]


def test_a_failed_click_is_reported_as_a_failure():
    button = make_element(1, "Button", "Save")
    obs = make_observation(button)
    with patch.object(screen_tools, "fresh_observation", return_value=obs), \
            patch.object(screen_tools, "_active_window", return_value="Notepad"), \
            patch("jarvis.tools.registry.execute") as execute:
        execute.return_value = MagicMock(ok=False, message="access denied")
        result = screen_tools.run("click_target", {"target": "Save"}, MagicMock())
    assert result["ok"] is False
    assert "access denied" in result["error"]


# -------------------------------------------------------------------------- #
# the dry run
# -------------------------------------------------------------------------- #

def test_look_at_screen_never_clicks():
    button = make_element(1, "Button", "Save", bbox=(0, 0, 40, 40))
    result = run_tool("look_at_screen", {"target": "Save"},
                      obs=make_observation(button))
    assert result["ok"] is True
    assert dispatched(result) == []
    assert "(20,20)" in result["result"]
    assert "dry run" in result["result"]


def test_look_at_screen_lists_the_real_names():
    elements = [
        make_element(1, "Button", "Save", bbox=(0, 0, 40, 20)),
        make_element(2, "Edit", "Search", bbox=(0, 40, 200, 60)),
    ]
    result = run_tool("look_at_screen", {}, obs=make_observation(*elements))
    assert "Save" in result["result"] and "Search" in result["result"]
    assert "Notepad" in result["result"]


def test_look_at_screen_can_filter_a_long_list():
    elements = [make_element(i, "Button", f"Item {i}") for i in range(1, 6)]
    elements.append(make_element(99, "Button", "Save"))
    result = run_tool("look_at_screen", {"query": "save"},
                      obs=make_observation(*elements))
    assert "Save" in result["result"]
    assert "Item 1" not in result["result"]


def test_look_at_screen_reports_when_nothing_matches_the_filter():
    result = run_tool("look_at_screen", {"query": "zzz"},
                      obs=make_observation(make_element(1, "Button", "Save")))
    assert result["ok"] is True
    assert "No controls matched" in result["result"]


def test_a_long_listing_is_truncated_with_a_count():
    elements = [make_element(i, "Button", f"Item {i}") for i in range(1, 200)]
    result = run_tool("look_at_screen", {}, obs=make_observation(*elements))
    assert f"+{199 - screen_tools._MAX_LISTED} more" in result["result"]


# -------------------------------------------------------------------------- #
# typing, keys, scrolling
# -------------------------------------------------------------------------- #

def test_type_into_focuses_the_field_first_then_types():
    """Focusing by resolved id is what stops text landing in another window."""
    field = make_element(4, "Edit", "Search", bbox=(0, 0, 200, 30))
    result = run_tool("type_into", {"target": "Search", "text": "hello"},
                      obs=make_observation(field))

    assert result["ok"] is True
    assert dispatched(result) == [
        ("click", {"element": 4}),
        ("type", {"text": "hello"}),
    ]


def test_type_into_can_submit():
    field = make_element(1, "Edit", "Search")
    result = run_tool("type_into", {"target": "Search", "text": "hi", "submit": True},
                      obs=make_observation(field))
    assert dispatched(result) == [
        ("click", {"element": 1}),
        ("type", {"text": "hi"}),
        ("press", {"keys": "enter"}),
    ]
    assert "pressed Enter" in result["result"]


def test_type_into_does_not_type_when_focus_fails():
    """A refused focus must not fall through to typing into whatever had focus."""
    field = make_element(1, "Edit", "Search")
    obs = make_observation(field)
    with patch.object(screen_tools, "fresh_observation", return_value=obs), \
            patch.object(screen_tools, "_active_window", return_value="Notepad"), \
            patch("jarvis.tools.registry.execute") as execute:
        execute.return_value = MagicMock(ok=False, message="element is gone")
        result = screen_tools.run("type_into", {"target": "Search", "text": "hi"},
                                  MagicMock())
    assert result["ok"] is False
    assert [call.args[0] for call in execute.call_args_list] == ["click"]


def test_type_into_says_it_cannot_verify_a_plain_edit_field():
    field = make_element(1, "Edit", "Search")
    result = run_tool("type_into", {"target": "Search", "text": "hi"},
                      obs=make_observation(field))
    assert "verify" in result["result"]


def test_type_into_refuses_an_empty_string():
    field = make_element(1, "Edit", "Search")
    result = run_tool("type_into", {"target": "Search", "text": ""},
                      obs=make_observation(field))
    assert result["ok"] is False
    assert "needs 'text'" in result["error"]


def test_press_keys_reports_which_window_received_it():
    result = run_tool("press_keys", {"keys": "ctrl+s"})
    assert result["ok"] is True
    assert dispatched(result) == [("press", {"keys": "ctrl+s"})]
    assert "Notepad" in result["result"]


def test_press_keys_needs_keys():
    result = run_tool("press_keys", {})
    assert result["ok"] is False
    assert "needs 'keys'" in result["error"]


def test_scroll_is_clamped_to_what_the_registry_accepts():
    """The registry clamps to +-50; an out-of-range value would be silent drift."""
    assert dispatched(run_tool("scroll_window", {"dy": 5})) == [
        ("scroll", {"dy": 5, "dx": 0})
    ]
    assert dispatched(run_tool("scroll_window", {"dy": 500})) == [
        ("scroll", {"dy": 50, "dx": 0})
    ]
    assert dispatched(run_tool("scroll_window", {"dy": -900, "dx": 12})) == [
        ("scroll", {"dy": -50, "dx": 12})
    ]
    # Unparseable input falls back to the default rather than raising.
    assert dispatched(run_tool("scroll_window", {"dy": "lots"})) == [
        ("scroll", {"dy": 3, "dx": 0})
    ]


def test_scroll_tells_the_model_its_element_list_is_now_stale():
    result = run_tool("scroll_window", {"dy": 3})
    assert "look_at_screen" in result["result"]


# -------------------------------------------------------------------------- #
# freshness
# -------------------------------------------------------------------------- #

def test_acting_invalidates_the_cached_list():
    """A click changes the screen, so the next resolution must re-read it."""
    button = make_element(1, "Button", "Save")
    result = run_tool("click_target", {"target": "Save"},
                      obs=make_observation(button))
    assert result["ok"] is True
    assert screen_tools._CACHE["observation"] is None


def test_observation_is_reused_within_the_freshness_window():
    """One action reads the list once, not once per step of the action."""
    observation = make_observation(make_element(1, "Edit", "Search"))
    with patch("jarvis.perception.elements.observe", return_value=observation) as observe:
        first = screen_tools.fresh_observation(MagicMock())
        second = screen_tools.fresh_observation(MagicMock())
    assert first is second
    assert observe.call_count == 1


def test_a_stale_observation_is_re_read():
    observation = make_observation(make_element(1, "Edit", "Search"))
    with patch("jarvis.perception.elements.observe", return_value=observation) as observe:
        screen_tools.fresh_observation(MagicMock())
        screen_tools.fresh_observation(MagicMock(), max_age=-1)
    assert observe.call_count == 2


def test_the_perception_cap_comes_from_config():
    settings = MagicMock()
    settings.perception.max_elements = 12
    observation = make_observation()
    with patch("jarvis.perception.elements.observe", return_value=observation) as observe:
        screen_tools.fresh_observation(settings)
    assert observe.call_args.kwargs["max_elements"] == 12


def test_a_broken_config_falls_back_to_the_schema_default():
    settings = MagicMock()
    settings.perception.max_elements = "many"
    observation = make_observation()
    with patch("jarvis.perception.elements.observe", return_value=observation) as observe:
        screen_tools.fresh_observation(settings)
    assert observe.call_args.kwargs["max_elements"] == 60


# -------------------------------------------------------------------------- #
# robustness and wiring
# -------------------------------------------------------------------------- #

def test_handlers_never_raise():
    """The caller is a language model; a perception bug must not kill the turn."""
    junk = [
        None, "not a dict", 12, [], {"target": {"nested": True}},
        {"target": "Save", "count": "two"}, {"dy": object()},
        {"query": ["a", "b"]}, {"target": "\x00\ud800"},
    ]
    for name in screen_tools.names():
        for args in junk:
            for obs in (make_observation(), make_observation(
                    make_element(1, "Button", "Save"))):
                result = run_tool(name, args, obs=obs)
                assert isinstance(result, dict), (name, args)
                assert isinstance(result["ok"], bool)
                assert isinstance(result["result"], str)
                assert isinstance(result["error"], str)


def test_a_dispatch_failure_becomes_words_not_an_exception():
    button = make_element(1, "Button", "Save")
    obs = make_observation(button)
    with patch.object(screen_tools, "fresh_observation", return_value=obs), \
            patch.object(screen_tools, "_active_window", return_value="Notepad"), \
            patch("jarvis.tools.registry.execute", side_effect=RuntimeError("no desktop")):
        result = screen_tools.run("click_target", {"target": "Save"}, MagicMock())
    assert result["ok"] is False
    assert "no desktop" in result["error"]


def test_unknown_screen_tool_is_refused():
    result = screen_tools.run("click_at_a_pixel", {}, MagicMock())
    assert result["ok"] is False
    assert "unknown screen tool" in result["error"]


def test_non_object_args_are_refused():
    result = screen_tools.run("click_target", "Save", MagicMock())
    assert result["ok"] is False


def test_click_target_reports_a_window_change_as_verification():
    button = make_element(1, "Button", "Open Settings")
    obs = make_observation(button, active_window="Desktop")
    with patch.object(screen_tools, "fresh_observation", return_value=obs), \
            patch.object(screen_tools, "_active_window",
                         side_effect=["Desktop", "Settings"]), \
            patch("jarvis.tools.registry.execute") as execute:
        execute.return_value = MagicMock(ok=True, message="clicked")
        result = screen_tools.run("click_target", {"target": "Open Settings"},
                                  MagicMock())
    assert result["ok"] is True
    assert "changed" in result["result"]
    assert "Settings" in result["result"]


def test_screen_tools_reach_the_registry_through_direct_tools():
    """The voice path calls direct_tools, so the wiring must be on that door."""
    for name in screen_tools.names():
        assert direct_tools.is_direct(name), name

    button = make_element(1, "Button", "Save")
    obs = make_observation(button)
    with patch.object(screen_tools, "fresh_observation", return_value=obs), \
            patch.object(screen_tools, "_active_window", return_value="Notepad"), \
            patch("jarvis.tools.registry.execute") as execute, \
            patch("jarvis.config.load_config", return_value=MagicMock()):
        execute.return_value = MagicMock(ok=True, message="clicked")
        result = direct_tools.run("click_target", {"target": "Save"})

    assert result["ok"] is True
    assert [call.args[0] for call in execute.call_args_list] == ["click"]
    assert result["state"] == "running"


def test_the_raw_primitives_stay_unreachable():
    """Closing the gap must not have re-opened blind coordinate clicking."""
    for name in ("click", "double_click", "right_click", "move", "drag",
                 "scroll", "type", "press", "key_sequence", "observe"):
        assert direct_tools.is_direct(name) is False, name
        result = direct_tools.run(name, {"x": 10, "y": 10})
        assert result["ok"] is False
        assert "not callable as a direct tool" in result["error"]


def test_every_declared_screen_tool_has_a_handler():
    """A declaration without a handler is a tool the model can call and lose."""
    for name in screen_tools.names():
        assert name in screen_tools._HANDLERS, name


def test_declarations_carry_a_usable_description_and_arguments():
    for declaration in screen_tools.declarations():
        assert declaration["description"].strip()
        assert declaration["arguments"]
        for argument in declaration["arguments"]:
            assert argument["name"]
            assert argument["description"].strip()
            assert argument["description"].split(":", 1)[0] in (
                "string", "integer", "number", "boolean", "object", "array"
            )


def test_required_arguments_are_marked_required():
    """A required argument must not be advertised as optional."""
    by_name = {tool["name"]: tool for tool in screen_tools.declarations()}
    click_target = {arg["name"]: arg for arg in by_name["click_target"]["arguments"]}
    assert "(optional" not in click_target["target"]["description"]
    assert "(optional" in click_target["count"]["description"]
    assert "(optional, default 1)" in click_target["count"]["description"]


def test_both_live_transports_offer_the_same_screen_tools():
    """Fish client tools and the realtime specs must not diverge."""
    from jarvis.live import prompts

    realtime = {tool["name"] for tool in prompts.get_direct_voice_tools()}
    fish = {tool["name"] for tool in direct_tools.declarations()}
    assert set(screen_tools.names()) <= realtime
    assert set(screen_tools.names()) <= fish


def test_the_realtime_spec_marks_required_parameters():
    from jarvis.live import prompts

    spec = next(tool for tool in prompts.get_direct_voice_tools()
                if tool["name"] == "click_target")
    assert spec["parameters"]["properties"]["target"]["type"] == "string"
    assert spec["parameters"]["required"] == ["target"]
    assert spec["parameters"]["properties"]["count"]["type"] == "integer"


def test_the_prompt_puts_direct_tools_before_delegation():
    """The agent delegated every click because nothing told it not to.

    It had the tools, but the prompt never ranked them, so the catch-all
    'execute_task' won by default and the user waited seconds for a click that
    should have taken under one.
    """
    from jarvis.live.prompts import build_live_voice_system_prompt

    prompt = build_live_voice_system_prompt()
    assert "ORDER OF PREFERENCE" in prompt
    assert "execute_task' last" in prompt
    assert "Never delegate a request that a direct tool covers" in prompt
    # Worked examples carry more weight than a rule for a small model, so the
    # prompt has to show both directions, not just the preference.
    assert "click the Save button" in prompt
    assert "None of these go to" in prompt
    assert "log into my bank" in prompt


def test_the_system_prompt_teaches_naming_over_guessing():
    from jarvis.live.prompts import build_live_voice_system_prompt

    prompt = build_live_voice_system_prompt()
    for name in screen_tools.names():
        assert name in prompt, name
    assert "NEVER guess a coordinate" in prompt
    # The foreground-window rule is stated, because discovering it by failing is
    # a wasted turn every single time.
    assert "focus_window" in prompt
    assert "foreground" in prompt
    # The delegation rule must no longer claim clicking needs the main agent.
    assert "DELEGATE THE WORK THAT NEEDS EYES" in prompt


def test_a_short_word_buried_in_a_long_label_is_a_coincidence_not_a_match():
    """Measured on a live screen, not invented.

    Reading the Jarvis UI returned its own transcribed log as text elements, and
    the request "prompt" matched `🎙️ Voice AI Agent prompted Main Agent: ...` at
    0.63 confidence. ``click_target("prompt")`` would have clicked a *log entry*
    while the user asked for an input - the confident-but-wrong click that makes
    a screen agent untrustworthy. A one-word request inside a long label has to
    fall below the floor.
    """
    for name in (
        '🎙️ Voice AI Agent prompted Main Agent: "Click on the chat input box"',
        "brain hiccup (attempt 1/5): Gemini API returned HTTP 429",
    ):
        element = make_element(1, "Text", name, interactive=False)
        assert screen_tools.score_element(element, "prompt") < screen_tools.MIN_CONFIDENCE
        match, detail = screen_tools.resolve(make_observation(element), "prompt")
        assert match is None, name
        assert "nothing on screen matches" in detail


def test_a_partial_name_that_does_cover_the_label_still_resolves():
    """The fix must not make ordinary partial names unusable."""
    for role, name, target in (
        ("Button", "Save As", "save"),
        ("Button", "Search settings", "search"),
        ("Button", "Open command palette", "open"),
        ("Text", "Battery status indicator", "battery"),
        ("Button", "Toggle Live Voice Mode (gpt-realtime)", "live voice"),
    ):
        element = make_element(1, role, name)
        assert screen_tools.score_element(element, target) >= screen_tools.MIN_CONFIDENCE, name
        match, _ = screen_tools.resolve(make_observation(element), target)
        assert match is element, name
