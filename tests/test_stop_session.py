"""Jarvis ending its own session.

Every other way a session stops comes from outside the agent: the END SESSION
button, ``:quit`` on stdin, the parent killing the child. This is the agent
deciding, mid-task, that its own session should end - so the things worth
pinning are the ones that make that safe and honest:

* the ask is a *request*, not an in-handler kill, so the runtime that owns the
  session closes it after the current step is written down;
* the first reason wins, because it is the last thing the user reads;
* only the declared boolean counts - a truthy object must never be read as
  "end the session";
* the voice agent cannot do it directly, because a live model must not be able
  to hang up on the user mid-sentence.
"""

from __future__ import annotations

import io
import json
import tempfile
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

import pytest

import jarvis.session_control as session_control
from jarvis.agent.loop import Agent, _asked_to_stop
from jarvis.config import Config
from jarvis.live import direct_tools
from jarvis.tools import registry
from jarvis.tools.schema import ACTIONS_BY_NAME


@pytest.fixture(autouse=True)
def _clean_request():
    """No test may inherit a stop request from another one."""
    session_control.clear_session_stop()
    yield
    session_control.clear_session_stop()


# --------------------------------------------------------------------------- #
# the declaration
# --------------------------------------------------------------------------- #

def test_stop_session_is_declared_and_bound():
    assert "stop_session" in ACTIONS_BY_NAME
    assert registry.handler_for("stop_session") is not None


def test_reason_is_optional_so_a_stop_is_never_blocked_on_wording():
    action = ACTIONS_BY_NAME["stop_session"]
    assert [p.name for p in action.params] == ["reason"]
    reason = action.params[0]
    assert reason.required is False
    assert reason.default == ""


def test_stop_session_is_not_a_terminal_action():
    """``terminal`` means "this task is done", which a stop is not.

    Keeping it out of that set is what stops a stop from being handed to the
    task verifier as a completed piece of work.
    """
    assert ACTIONS_BY_NAME["stop_session"].terminal is False
    assert {a.name for a in ACTIONS_BY_NAME.values() if a.terminal} == {"finish", "ask"}


# --------------------------------------------------------------------------- #
# the handler
# --------------------------------------------------------------------------- #

def test_handler_requests_the_stop_rather_than_ending_the_session_itself():
    result = registry.execute("stop_session", {"reason": "Done for now."}, None, None)

    assert result.ok is True
    assert result.stop_session is True
    # Not finished: the run must not be treated as a verified success.
    assert result.finished is False
    assert result.needs_observe is False
    assert session_control.session_stop_requested() is True
    assert session_control.session_stop_reason() == "Done for now."
    assert session_control.session_stop_source() == "agent"


def test_reason_is_normalised_before_it_is_stored():
    registry.execute("stop_session", {"reason": "  shutting   it\n down  "}, None, None)
    assert session_control.session_stop_reason() == "shutting it down"


def test_a_blank_or_missing_reason_still_says_something():
    registry.execute("stop_session", {}, None, None)
    assert session_control.session_stop_reason() == "Stopping this session as requested."

    session_control.clear_session_stop()
    registry.execute("stop_session", {"reason": "   "}, None, None)
    assert session_control.session_stop_reason() == "Stopping this session as requested."


def test_the_first_reason_wins_and_later_calls_say_so():
    first = registry.execute("stop_session", {"reason": "saved the report first"}, None, None)
    second = registry.execute("stop_session", {"reason": "actually, something else"}, None, None)

    assert "saved the report first" in first.message
    # The user is about to read this: a retry must not rewrite it.
    assert session_control.session_stop_reason() == "saved the report first"
    assert "already stopping" in second.message
    assert second.stop_session is True


def test_the_handler_does_not_need_an_observation_or_config():
    """It is callable from the direct-tool path, where neither exists."""
    assert registry.handler_for("stop_session")({"reason": "x"}, None, None).ok is True


# --------------------------------------------------------------------------- #
# the request
# --------------------------------------------------------------------------- #

def test_clear_forgets_the_request():
    session_control.request_session_stop("bye")
    session_control.clear_session_stop()
    assert session_control.session_stop_requested() is False
    assert session_control.session_stop_reason() is None
    assert session_control.session_stop_source() is None


def test_the_snapshot_is_a_copy_not_a_handle_on_the_state():
    session_control.request_session_stop("bye")
    snapshot = session_control.session_stop_record()
    snapshot["reason"] = "tampered"
    assert session_control.session_stop_reason() == "bye"


def test_a_listener_is_told_once():
    seen: list[dict] = []
    session_control.add_session_stop_listener(seen.append)

    session_control.request_session_stop("one")
    session_control.request_session_stop("two")

    assert [record["reason"] for record in seen] == ["one"]


def test_a_listener_registered_after_the_fact_still_learns_the_session_is_ending():
    """Otherwise a late runtime waits for a second request that never comes."""
    session_control.request_session_stop("bye")
    seen: list[dict] = []
    session_control.add_session_stop_listener(seen.append)
    assert [record["reason"] for record in seen] == ["bye"]


def test_a_listener_that_raises_does_not_break_the_request():
    def broken(_record):
        raise RuntimeError("listener exploded")

    session_control.add_session_stop_listener(broken)
    session_control.request_session_stop("bye")
    assert session_control.session_stop_requested() is True


def test_a_listener_may_re_enter_without_deadlocking():
    """Listeners fire outside the lock; a re-entrant one must not hang."""
    def re_enter(record):
        session_control.request_session_stop("second", source="listener")

    session_control.add_session_stop_listener(re_enter)
    session_control.request_session_stop("first")
    assert session_control.session_stop_reason() == "first"


def test_unsubscribing_stops_the_listener_firing():
    seen: list[dict] = []
    unsubscribe = session_control.add_session_stop_listener(seen.append)
    unsubscribe()
    session_control.request_session_stop("bye")
    assert seen == []


# --------------------------------------------------------------------------- #
# the loop
# --------------------------------------------------------------------------- #

def test_only_a_real_true_counts_as_a_stop_signal():
    """A truthy stand-in must never be read as "end the session".

    The agent loop is driven by test doubles in several suites; the stop is the
    one signal where guessing would silently end a real user's session.
    """
    assert _asked_to_stop(Mock()) is False
    assert _asked_to_stop(Mock(stop_session="yes")) is False
    assert _asked_to_stop(Mock(stop_session=1)) is False
    assert _asked_to_stop(object()) is False
    assert _asked_to_stop(registry.ActionResult(True, "hi")) is False
    assert _asked_to_stop(registry.ActionResult(True, "hi", stop_session=True)) is True


_STOP_DECISION = json.dumps({
    "thought": "The user asked me to shut down.",
    "action": "stop_session",
    "args": {"reason": "Shutting down now."},
})


def _stop_agent_brain():
    """A brain that classifies the prompt as a task, then stops the session.

    The chat/task classifier asks the model before the first step, so a bare
    list of responses would be consumed by the wrong call - and the retry
    wrapper would then ask again and get nothing. Answering by *what is being
    asked* keeps this deterministic however many times the loop retries.
    """
    brain = Mock()

    def complete(system, messages, image=None, **_kwargs):
        if "ordinary CONVERSATION" in str(system):
            return '{"mode":"task"}'
        return _STOP_DECISION

    brain.complete.side_effect = complete
    return brain


def _perceived():
    obs = Mock()
    obs.active_window = "Desktop"
    obs.screen_size = (1920, 1080)
    obs.elements = []
    obs.menu.return_value = ""
    return obs


def _build_agent(brain):
    cfg = Config()
    cfg.data.collect_trajectories = False
    cfg.data.trajectory_dir = tempfile.mkdtemp()
    return Agent(brain, cfg)


def test_run_ends_on_a_stop_and_never_reaches_the_verifier():
    agent = _build_agent(_stop_agent_brain())
    events: list[dict] = []

    with patch.object(agent, "_perceive", return_value=_perceived()), \
         patch.object(agent, "_verify_success") as verify:
        result = agent.run("shut jarvis down", on_progress=events.append)

    assert "Shutting down now." in result
    assert session_control.session_stop_requested() is True
    # A stop is not a completed task: verifying it would be a category error.
    verify.assert_not_called()
    assert "session_stop" in [event["event"] for event in events]


def test_the_runtime_is_told_which_reason_to_show():
    agent = _build_agent(_stop_agent_brain())
    events: list[dict] = []

    with patch.object(agent, "_perceive", return_value=_perceived()), \
         patch.object(agent, "_verify_success"):
        agent.run("shut jarvis down", on_progress=events.append)

    reason_events = [e for e in events if e["event"] == "session_stop"]
    assert reason_events, "the runtime never heard why the session stopped"
    assert "Shutting down now." in str(reason_events[0].get("reason", ""))


def test_an_ordinary_run_leaves_the_stop_request_untouched():
    brain = Mock()

    def complete(system, messages, image=None, **_kwargs):
        if "ordinary CONVERSATION" in str(system):
            return '{"mode":"task"}'
        return json.dumps({"thought": "done", "action": "finish",
                           "args": {"summary": "nothing to do"}})

    brain.complete.side_effect = complete
    agent = _build_agent(brain)

    with patch.object(agent, "_perceive", return_value=_perceived()), \
         patch.object(agent, "_verify_success", return_value=(True, "ok")), \
         patch("jarvis.tools.registry.execute",
               return_value=registry.ActionResult(True, "nothing to do",
                                                  finished=True, needs_observe=False)):
        agent.run("do nothing")

    assert session_control.session_stop_requested() is False


# --------------------------------------------------------------------------- #
# what the user reads
# --------------------------------------------------------------------------- #

def test_the_closing_note_is_printed_where_the_user_can_see_it():
    from jarvis.console import _announce_session_stop

    session_control.request_session_stop("Reports saved; closing down.", source="agent")
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        _announce_session_stop()

    printed = buffer.getvalue()
    assert "Reports saved; closing down." in printed
    assert "session stopped by jarvis" in printed


# --------------------------------------------------------------------------- #
# the browser parent
# --------------------------------------------------------------------------- #

def _bridge_with_recording_broker():
    from jarvis.browser import TerminalBridge

    bridge = TerminalBridge(token="stop-session-test")
    published: list[tuple[str, dict]] = []
    bridge.broker.publish = lambda event, **payload: published.append((event, payload))
    return bridge, published


def test_the_parent_closes_the_child_when_the_agent_announces_a_stop():
    """The child is parked on its input pipe; the parent has to end it.

    The hand-off is deferred off the reader thread (writing to the pipe from
    the thread that reads it deadlocks the child's next read), so this waits
    for the timer rather than asserting on the call in-line.
    """
    import time

    bridge, _published = _bridge_with_recording_broker()
    bridge.request_shutdown = Mock(return_value=(True, "shutdown requested"))

    bridge._handle_structured({
        "event": "session_stop",
        "reason": "Shutting down now.",
        "source": "agent",
    })

    deadline = time.time() + 2.0
    while not bridge.request_shutdown.called and time.time() < deadline:
        time.sleep(0.02)
    assert bridge.request_shutdown.called is True


def test_the_stop_event_still_reaches_the_page():
    bridge, published = _bridge_with_recording_broker()
    bridge.request_shutdown = Mock(return_value=(True, "shutdown requested"))

    bridge._handle_structured({
        "event": "session_stop",
        "reason": "Shutting down now.",
        "source": "agent",
    })

    assert [event for event, _payload in published] == ["session_stop"]
    assert published[0][1]["reason"] == "Shutting down now."


# --------------------------------------------------------------------------- #
# the voice path
# --------------------------------------------------------------------------- #

def test_the_voice_agent_cannot_stop_the_session_directly():
    assert direct_tools.is_direct("stop_session") is False
    assert direct_tools.EXCLUDED["stop_session"] == "session"
    assert "stop_session" not in direct_tools.names()

    refused = direct_tools.run("stop_session", {"reason": "hang up"})
    assert refused["ok"] is False
    assert "execute_task" in refused["error"]
    # The refusal must not have taken effect as a side effect of trying.
    assert session_control.session_stop_requested() is False


def test_the_voice_tool_set_did_not_grow():
    """Excluding it keeps the published Fish agent's 65 tools valid.

    A new callable action here would silently make the hosted voice agent's
    tool list stale, so the count is pinned rather than assumed.
    """
    from jarvis.live import screen_tools

    assert len(direct_tools.names()) == 62
    assert len(direct_tools.names()) + len(screen_tools.names()) == 67
