"""The tool choreography: every action narrates itself, and none of it costs.

Two things are pinned here, because they are the two ways this feature could
fail:

  * **coverage** - every action the model can emit has a script that renders.
    A new action added to ``schema.py`` without one should fail this file rather
    than quietly animate as nothing;
  * **cost** - the animator sits on the path of *every* tool call, so a tool must
    never wait for it: ``begin``/``finish`` are queue puts, a missing HUD is a
    shared no-op, and a broken animator must not be able to fail the action it
    was decorating.
"""

from __future__ import annotations

import time

import pytest

from jarvis.config import Config
from jarvis.hud import choreography as ch
from jarvis.tools.schema import ACTIONS


@pytest.fixture(autouse=True)
def _sink():
    """Collect beats in a list instead of touching a HUD, browser or TTY."""
    seen: list[dict] = []
    ch.set_sink(seen.append)
    yield seen
    ch.set_sink(None)


def _wait(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# --------------------------------------------------------------------------- #
# Coverage
# --------------------------------------------------------------------------- #

def test_every_declared_action_has_a_script():
    """All 92 actions, and none of them left to the generic fallback.

    The fallback exists so a *newly added* action still animates - but a shipped
    action relying on it means the narration is "⚙ run_command" for a tool whose
    real steps are known, which is the thing this feature was asked to fix.
    """
    unscripted = [a.name for a in ACTIONS if a.name not in ch.SCRIPTS]
    assert unscripted == []


def test_scripts_stay_in_step_with_the_schema():
    """A script must not outlive its action: stale entries are dead weight."""
    declared = {a.name for a in ACTIONS}
    assert set(ch.SCRIPTS) - declared == set()


@pytest.mark.parametrize("action", ACTIONS, ids=lambda a: a.name)
def test_every_beat_renders_for_the_actions_own_examples(action):
    """Beat labels are templates; the schema's own examples must fill them.

    This is the test that catches a typo like ``{patttern}`` - which would
    otherwise render as the placeholder default forever, invisibly.
    """
    beats = ch.beats_for(action.name, action.category)
    assert beats, f"{action.name} has no beats"

    examples = action.examples or ({},)
    for beat in beats:
        for example in examples:
            label = ch.label_for(beat, example)
            assert label, f"{action.name}: empty label"
            assert "{" not in label and "}" not in label, (action.name, label)
            assert len(label) <= ch._LABEL_CHARS, (action.name, label)


def test_a_pointer_target_renders_however_it_was_given():
    """A saved name, an element id and raw pixels are one concept to a viewer."""
    beat = ch.Beat("🎯", "aiming at {where}")

    assert ch.label_for(beat, {}) == "aiming at the target"
    assert ch.label_for(beat, {"element": 4}) == "aiming at element 4"
    assert ch.label_for(beat, {"coord": "whatsapp-send-button"}) == \
        "aiming at the saved target 'whatsapp-send-button'"
    assert ch.label_for(beat, {"x": 640, "y": 360}) == "aiming at 640,360"
    # A label with no placeholders is passed through untouched.
    assert ch.label_for(ch.Beat("⚡", "clicking"), {}) == "clicking"


def test_whatever_the_model_sends_cannot_break_a_label():
    """Arguments are model output: a dict, a number or a 10 kB string is fine."""
    beat = ch.Beat("⌨", "running: {command}")
    assert ch.label_for(beat, {"command": {"a": 1}}) == "running: {'a': 1}"
    assert ch.label_for(beat, {"command": 5}) == "running: 5"
    huge = ch.label_for(beat, {"command": "x" * 5000})
    assert len(huge) <= ch._LABEL_CHARS


def test_an_argument_under_another_key_is_still_narrated():
    """``open_app`` is declared with ``name``, but callers may send ``app``."""
    beat = ch.Beat("🔍", "scanning the Start Menu index for '{name}'")

    assert ch.label_for(beat, {"app": "calculator"}) == \
        "scanning the Start Menu index for 'calculator'"


def test_a_list_argument_is_counted_not_printed():
    beat = ch.Beat("⌨", "pressing {n} keys in sequence")
    assert ch.label_for(beat, {"keys": ["ctrl+a", "ctrl+c"]}) == \
        "pressing 2 keys in sequence"


# --------------------------------------------------------------------------- #
# Cost
# --------------------------------------------------------------------------- #

def test_begin_is_free_when_nothing_is_watching(monkeypatch):
    """No sink, no HUD, no TTY: the no-op handle, at dictionary-lookup cost."""
    ch.set_sink(None)
    monkeypatch.setattr(ch, "_has_audience", lambda: False)

    started = time.perf_counter()
    for _ in range(500):
        animator = ch.begin("open_app", {"name": "notepad"}, None)
    elapsed = time.perf_counter() - started

    assert animator is ch.NULL
    assert elapsed < 0.2, f"500 begins took {elapsed:.3f}s"


def test_the_hook_cannot_be_the_reason_a_tool_feels_slow(_sink):
    """begin + finish are queue puts: microseconds, not milliseconds."""
    animator = ch.begin("open_app", {"name": "notepad"}, None)
    started = time.perf_counter()
    for _ in range(50):
        ch.begin("open_app", {"name": "notepad"}, None).finish(True, "launched")
    elapsed = (time.perf_counter() - started) / 50

    assert animator is not ch.NULL
    assert elapsed < 0.005, f"begin+finish averaged {elapsed * 1000:.2f} ms"


def test_a_broken_animator_never_fails_the_action(monkeypatch):
    """The decoration must not be able to take down the thing it decorates."""
    from jarvis.tools import registry

    monkeypatch.setattr(ch, "begin",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("hud died")))

    result = registry.execute("regex_intel",
                              {"op": "test", "pattern": r"^v\d+$", "text": "v1"},
                              None, Config())

    assert result.ok or result.message, "the tool still ran and returned"


# --------------------------------------------------------------------------- #
# Scripts that run
# --------------------------------------------------------------------------- #

def test_open_app_narrates_the_index_launch_and_window_steps(_sink):
    """The example this feature was asked for, beat by beat."""
    animator = ch.begin("open_app", {"name": "capcut"}, None)
    assert _wait(lambda: len(_sink) >= 1)

    first = _sink[0]
    assert first["glyph"] == "🔍"
    assert "'capcut'" in first["label"]
    assert first["state"] == "perceiving"

    animator.finish(True, 'launched \'capcut\' ("CapCut" via Start Menu); '
                          "window 'CapCut' is up")
    assert _wait(lambda: any(p["closing"] for p in _sink))

    closing = [p for p in _sink if p["closing"]][-1]
    assert closing["state"] == "success"
    assert closing["glyph"] == "✓"
    assert "window 'CapCut' is up" in closing["label"]


def test_the_closing_beat_carries_the_real_result_verbatim(_sink):
    """Failures are shown as failures, and braces in a result survive."""
    animator = ch.begin("data_validate", {"op": "infer", "data": "x"}, None)
    animator.finish(False, 'invalid JSON at line 3: {"a": }')
    assert _wait(lambda: any(p["closing"] for p in _sink))

    closing = [p for p in _sink if p["closing"]][-1]
    assert closing["state"] == "warning"
    assert closing["glyph"] == "⚠"
    assert '{"a": }' in closing["label"]


def test_a_new_action_preempts_the_one_before_it(_sink):
    """Two tools in quick succession must not interleave their scripts."""
    first = ch.begin("click", {"element": 3}, None)
    second = ch.begin("scroll", {"dy": 5}, None)
    first.finish(True, "clicked")          # late finish from the preempted tool
    second.finish(True, "scrolled")

    assert _wait(lambda: any(p["closing"] for p in _sink))
    time.sleep(0.2)

    closing = [p for p in _sink if p["closing"]]
    assert [p["action"] for p in closing] == ["scroll"]
    assert all(p["action"] != "click" or not p["closing"] for p in _sink)


def test_animations_off_means_silence(_sink):
    """The user's switch wins over every surface, including an attached sink."""
    cfg = Config()
    cfg.hud.animations = False

    animator = ch.begin("open_app", {"name": "notepad"}, cfg)
    animator.finish(True, "launched 'notepad'")
    time.sleep(0.2)

    assert animator is ch.NULL
    assert _sink == []


def test_a_slow_tool_keeps_ticking_instead_of_freezing(_sink, monkeypatch):
    """Out of script but still running: heartbeats, so it never looks hung."""
    monkeypatch.setattr(ch, "BEAT_SECONDS", 0.01)
    animator = ch.begin("run_command", {"command": "long job"}, None)
    assert _wait(lambda: len(_sink) >= len(ch.SCRIPTS["run_command"]),
                 timeout=2.0)
    # Past the script, the worker switches to its heartbeat narration.
    assert _wait(lambda: any("still working" in p["label"] for p in _sink),
                 timeout=3.0)
    animator.finish(True, "done")
