"""Tests for the voice agent's direct action execution.

The voice agent does not need the perceive/think/act loop for deterministic
requests, so it calls Jarvis's actions itself. These tests pin the two things
that make that safe: the action set never includes something the voice model
cannot aim (or must not touch), and the round trip through the terminal runtime
returns the real result.
"""

from __future__ import annotations

import json
import re
import threading
from http import HTTPStatus
from unittest.mock import MagicMock, patch

import pytest

from jarvis.live import direct_tools, screen_tools
from jarvis.tools.schema import ACTIONS_BY_NAME


def test_every_excluded_name_is_a_real_action():
    """A typo in the exclusion list would silently leave an action callable."""
    unknown = sorted(set(direct_tools.EXCLUDED) - set(ACTIONS_BY_NAME))
    assert unknown == []


def test_screen_actions_cannot_be_called_directly():
    """Pointer/keyboard actions have no element list to aim at without the loop."""
    for name in ("click", "double_click", "type", "press", "scroll", "drag",
                 "key_sequence", "observe", "take_screenshot"):
        assert not direct_tools.is_direct(name), name
        result = direct_tools.run(name, {"x": 1, "y": 1})
        assert result["ok"] is False
        assert "not callable as a direct tool" in result["error"]


def test_self_modifying_and_secret_actions_cannot_be_called_directly():
    for name in ("self_upgrade", "synthesize_tool", "secret", "mouse_control"):
        assert not direct_tools.is_direct(name), name


def test_everything_else_is_callable():
    """Default-open: a new action becomes voice-callable unless excluded.

    The resolving screen tools are appended after the schema-derived set, so the
    first ``len(expected)`` names are still exactly the non-excluded actions in
    schema order - a new action cannot slip in unnoticed, and cannot be dropped
    by the append either.
    """
    expected = [name for name in ACTIONS_BY_NAME if name not in direct_tools.EXCLUDED]
    names = list(direct_tools.names())
    assert names[: len(expected)] == expected
    assert names[len(expected):] == list(screen_tools.names())
    # The screen tools are extra hands, not replacements: the raw primitives
    # stay out, so nothing here re-opens blind coordinate clicking.
    for name in screen_tools.names():
        assert name not in ACTIONS_BY_NAME, name
    for name in ("click", "type", "press", "scroll"):
        assert name not in names, name
    # A meaningful slice of the action space, not a token gesture.
    assert len(direct_tools.names()) >= 40
    for name in ("open_app", "run_command", "python", "read_file", "write_file",
                 "web_search", "read_url", "browser_action", "system_status",
                 "clipboard_read", "remember", "schedule_task"):
        assert direct_tools.is_direct(name), name


def test_declarations_are_generated_from_the_schema():
    """Argument names must be the ones the registry actually reads."""
    by_name = {tool["name"]: tool for tool in direct_tools.declarations()}
    for name in ("run_command", "read_file", "browser_action", "write_file"):
        action = ACTIONS_BY_NAME[name]
        assert [arg["name"] for arg in by_name[name]["arguments"]] == [
            parameter.name for parameter in action.params
        ]


def test_declarations_satisfy_the_fish_schema():
    for tool in direct_tools.declarations():
        assert tool["tool_type"] == "client"


def test_screen_tools_are_declared_alongside_the_schema_actions():
    """Both halves reach the model through the one declarations list."""
    declared = {tool["name"] for tool in direct_tools.declarations()}
    assert set(screen_tools.names()) <= declared
    for name in screen_tools.names():
        tool = next(t for t in direct_tools.declarations() if t["name"] == name)
        assert tool["tool_type"] == "client"
        assert tool["expects_response"] is True
        assert tool["timeout_seconds"] == direct_tools.TOOL_TIMEOUT_SECONDS
        assert tool["description"].strip()
        assert tool["arguments"], name
        for argument in tool["arguments"]:
            # The Fish convention encodes the type in the description prefix.
            assert re.match(r"^(string|integer|number|boolean|object|array): ",
                            argument["description"]), argument
        assert re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,63}", tool["name"])
        assert tool["description"]
        assert tool["expects_response"] is True
        assert tool["timeout_seconds"] == direct_tools.TOOL_TIMEOUT_SECONDS
        for argument in tool["arguments"]:
            assert set(argument) == {"name", "description"}
            assert argument["name"] and argument["description"]


def test_tool_names_are_unique_and_never_collide_with_the_control_tools():
    names = [tool["name"] for tool in direct_tools.declarations()]
    assert len(names) == len(set(names))
    assert not set(names) & {"execute_task", "cancel_task", "get_task_status"}


def test_client_deadline_is_inside_the_server_deadline():
    """The client must give up first, or the model is told a lie either way."""
    assert direct_tools.CLIENT_TOOL_TIMEOUT_MS < direct_tools.TOOL_TIMEOUT_SECONDS * 1000
    assert direct_tools.TOOL_TIMEOUT_SECONDS <= 120


def test_run_executes_through_the_registry():
    recorded = {}

    class FakeResult:
        ok = True
        message = "opened notepad"
        finished = False

    def fake_execute(name, args, obs, cfg):
        recorded.update(name=name, args=args)
        return FakeResult()

    with patch("jarvis.tools.registry.execute", side_effect=fake_execute), \
            patch("jarvis.config.load_config", return_value="CFG"):
        result = direct_tools.run("open_app", {"name": "notepad"})

    assert result == {"ok": True, "result": "opened notepad", "state": "running"}
    assert recorded == {"name": "open_app", "args": {"name": "notepad"}}


def test_run_reports_expected_failures_as_data():
    """The caller is a language model: a failure has to come back as words."""

    def boom(*_args, **_kwargs):
        raise RuntimeError("no display")

    with patch("jarvis.tools.registry.execute", side_effect=boom), \
            patch("jarvis.config.load_config", return_value="CFG"):
        result = direct_tools.run("system_status", {})

    assert result["ok"] is False
    assert "no display" in result["error"]


def test_run_refuses_unknown_actions():
    result = direct_tools.run("not_an_action", {})
    assert result["ok"] is False
    assert "unknown action" in result["error"]


def test_run_rejects_non_object_args():
    result = direct_tools.run("open_app", "notepad")
    assert result["ok"] is False


def test_clamp_args_caps_model_chosen_timeouts():
    """A direct call must not outlive the client-tool deadline."""
    assert direct_tools.clamp_args("run_command", {"timeout": 600})["timeout"] == 90
    assert direct_tools.clamp_args("python", {"timeout": 5})["timeout"] == 5
    assert direct_tools.clamp_args("python", {"timeout": "soon"}) == {}
    # Actions with no timeout argument are left alone.
    assert direct_tools.clamp_args("open_app", {"name": "chrome"}) == {"name": "chrome"}


def test_clamped_timeouts_stay_inside_the_deadline():
    for name, clamps in direct_tools._ARG_TIMEOUT_CLAMPS.items():
        for ceiling in clamps.values():
            assert ceiling < direct_tools.TOOL_TIMEOUT_SECONDS, name


def test_empty_observation_is_usable():
    obs = direct_tools.empty_observation()
    assert obs.elements == []
    assert obs.by_id(1) is None


# --------------------------------------------------------------------------- #
# round trip through the terminal runtime
# --------------------------------------------------------------------------- #

class _FakeStdin:
    """Captures the wire line and answers it the way the child process would."""

    def __init__(self, bridge):
        self.bridge = bridge
        self.written = []

    def write(self, data):
        self.written.append(data.decode("utf-8"))

    def flush(self):
        request = json.loads(self.written[-1].split(":", 1)[1])
        threading.Thread(
            target=self.bridge._resolve_tool_result,
            args=(
                {
                    "call_id": request["call_id"],
                    "ok": True,
                    "result": f"ran {request['tool']}",
                },
            ),
            daemon=True,
        ).start()


class _FakeProcess:
    def __init__(self, bridge):
        self.stdin = _FakeStdin(bridge)

    def poll(self):
        return None


def _live_bridge():
    from jarvis.browser import TerminalBridge

    bridge = TerminalBridge()
    bridge.process = _FakeProcess(bridge)
    bridge.accepting_input = True
    return bridge


def test_execute_tool_round_trips_through_the_child():
    from jarvis.browser import TOOL_PREFIX

    bridge = _live_bridge()
    result = bridge.execute_tool("system_status", {}, timeout=5)

    assert result == {"ok": True, "result": "ran system_status", "error": ""}
    request = json.loads(bridge.process.stdin.written[-1].split(":", 1)[1])
    assert bridge.process.stdin.written[-1].startswith(TOOL_PREFIX)
    assert request["tool"] == "system_status"
    assert request["args"] == {}


def test_execute_tool_refuses_while_jarvis_is_busy():
    """A direct call must never race the agentic loop over the same desktop."""
    bridge = _live_bridge()
    bridge.accepting_input = False

    result = bridge.execute_tool("open_app", {"name": "chrome"})

    assert result["ok"] is False
    assert "busy" in result["error"]
    assert bridge.process.stdin.written == []


def test_execute_tool_rejects_non_direct_actions_before_writing():
    bridge = _live_bridge()

    result = bridge.execute_tool("click", {"x": 5, "y": 5})

    assert result["ok"] is False
    assert bridge.process.stdin.written == []


def test_execute_tool_reports_a_dead_runtime():
    from jarvis.browser import TerminalBridge

    bridge = TerminalBridge()
    result = bridge.execute_tool("system_status")
    assert result["ok"] is False
    assert "not running" in result["error"]


def test_execute_tool_times_out_without_an_answer():
    """A wedged runtime must not park the HTTP thread forever."""
    from jarvis.browser import TerminalBridge

    class Silent:
        def __init__(self):
            self.stdin = self
            self.written = []

        def write(self, data):
            self.written.append(data)

        def flush(self):
            pass                     # deliberately never answers

        def poll(self):
            return None

    bridge = TerminalBridge()
    bridge.process = Silent()
    bridge.accepting_input = True

    result = bridge.execute_tool("system_status", {}, timeout=1)

    assert result["ok"] is False
    assert "did not finish" in result["error"]
    # The waiter must not be left behind.
    assert bridge._tool_waiters == {}


def test_tool_results_are_truncated_before_reaching_the_model():
    """Fish caps a client-tool result around 60 KB, and the model re-reads it
    every turn, so a huge result is trimmed rather than shipped whole."""
    from jarvis.browser import _MAX_TOOL_RESULT, TerminalBridge

    bridge = TerminalBridge()
    huge = "x" * (_MAX_TOOL_RESULT + 500)
    waiter = threading.Event()
    bridge._tool_waiters["c1"] = waiter

    result = bridge._resolve_tool_result({"call_id": "c1", "ok": True, "result": huge})

    assert waiter.is_set()
    assert "truncated" in result["result"]
    assert len(result["result"]) < _MAX_TOOL_RESULT + 100


def test_unclaimed_tool_results_are_published_to_the_ui():
    """A late answer to a timed-out call must still be visible in the log."""
    from jarvis.browser import TerminalBridge

    bridge = TerminalBridge()
    published: list = []
    with patch.object(bridge.broker, "publish", side_effect=lambda *a, **k: published.append(k)):
        bridge._resolve_tool_result({"call_id": "gone", "ok": False, "error": "boom"})

    assert published[0]["error"] == "boom"


# --------------------------------------------------------------------------- #
# POST /api/tool/execute
# --------------------------------------------------------------------------- #

def _handler(bridge=None):
    from jarvis.browser import BrowserRequestHandler

    handler = object.__new__(BrowserRequestHandler)
    handler._require_api_access = lambda require_origin=False: True
    responses: list = []
    handler._json = lambda status, body: responses.append((status, body))
    handler.server = MagicMock()
    handler.server.bridge = bridge or MagicMock()
    return handler, responses


def test_tool_execute_endpoint_runs_the_action():
    bridge = MagicMock()
    bridge.execute_tool.return_value = {"ok": True, "result": "notepad is open"}
    handler, responses = _handler(bridge)

    handler._handle_tool_execute({"tool": "open_app", "args": {"name": "notepad"}})

    status, body = responses[0]
    assert status == HTTPStatus.OK
    assert body["ok"] is True and body["result"] == "notepad is open"
    bridge.execute_tool.assert_called_once()


def test_tool_execute_endpoint_surfaces_a_failed_action_as_200():
    """A failed action is a valid result the voice agent must be able to report."""
    bridge = MagicMock()
    bridge.execute_tool.return_value = {"ok": False, "error": "no such file"}
    handler, responses = _handler(bridge)

    handler._handle_tool_execute({"tool": "read_file", "args": {"path": "nope"}})

    status, body = responses[0]
    assert status == HTTPStatus.OK
    assert body["ok"] is False and body["error"] == "no such file"


def test_tool_execute_endpoint_refuses_actions_outside_the_set():
    bridge = MagicMock()
    handler, responses = _handler(bridge)

    handler._handle_tool_execute({"tool": "self_upgrade", "args": {}})

    status, body = responses[0]
    assert status == HTTPStatus.FORBIDDEN
    assert "not an available direct tool" in body["error"]
    bridge.execute_tool.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"tool": "   "},
        {"tool": "open_app", "args": "notepad"},
        {"tool": "open_app", "args": [1, 2]},
    ],
)
def test_tool_execute_endpoint_rejects_bad_requests(payload):
    bridge = MagicMock()
    handler, responses = _handler(bridge)

    handler._handle_tool_execute(payload)

    assert responses[0][0] == HTTPStatus.BAD_REQUEST
    bridge.execute_tool.assert_not_called()


def test_tool_execute_endpoint_is_token_gated():
    handler, responses = _handler()
    handler._require_api_access = lambda require_origin=False: False

    handler._handle_tool_execute({"tool": "open_app", "args": {}})

    assert responses == []


def test_tool_execute_is_a_registered_post_route():
    """An unlisted path 404s before it ever reaches the handler."""
    import inspect

    from jarvis.browser import BrowserRequestHandler

    source = inspect.getsource(BrowserRequestHandler.do_POST)
    assert '"/api/tool/execute"' in source


# --------------------------------------------------------------------------- #
# the child process side
# --------------------------------------------------------------------------- #

def test_worker_runs_a_tool_request_and_emits_the_result():
    from jarvis import browser_worker

    events = []
    with patch.object(browser_worker, "emit", side_effect=lambda event, **kw: events.append((event, kw))), \
            patch("jarvis.live.direct_tools.run", return_value={"ok": True, "result": "done"}):
        browser_worker.run_tool_request(
            json.dumps({"call_id": "c1", "tool": "open_app", "args": {"name": "chrome"}})
        )

    event, payload = events[-1]
    assert event == "tool_result"
    assert payload["call_id"] == "c1"
    assert payload["ok"] is True
    assert payload["result"] == "done"


def test_worker_reports_a_malformed_request_instead_of_dying():
    from jarvis import browser_worker

    events = []
    with patch.object(browser_worker, "emit", side_effect=lambda event, **kw: events.append((event, kw))):
        browser_worker.run_tool_request("{not json")

    event, payload = events[-1]
    assert event == "tool_result"
    assert payload["ok"] is False
    assert payload["error"]


def test_worker_tool_prefix_is_distinct_from_the_input_prefix():
    from jarvis import browser_worker

    assert browser_worker.TOOL_PREFIX != browser_worker.INPUT_PREFIX
    assert not browser_worker.TOOL_PREFIX.startswith(browser_worker.INPUT_PREFIX)
    assert not browser_worker.INPUT_PREFIX.startswith(browser_worker.TOOL_PREFIX)


class DirectCallAnnouncementTests:
    """A direct call must be *visible* while it happens.

    The main loop shows every action with a ``log.act`` line; the voice path
    showed nothing at all, so a click the agent made was indistinguishable from
    a click that never happened - the exact ambiguity this whole feature exists
    to remove.
    """

    def test_successful_action_is_announced_with_its_result(self):
        with patch.object(
            direct_tools, "_dispatch", return_value={"ok": True, "result": "clicked Save"}
        ), patch("jarvis.utils.logging.ok") as ok:
            direct_tools.run("click_target", {"target": "Save"})

        message = ok.call_args[0][0]
        assert "click_target" in message
        assert "Save" in message
        assert "clicked Save" in message

    def test_refusal_is_announced_as_a_warning_not_a_success(self):
        with patch.object(
            direct_tools, "_dispatch", return_value={"ok": False, "error": "no match"}
        ), patch("jarvis.utils.logging.warn") as warn, patch(
            "jarvis.utils.logging.ok"
        ) as ok:
            direct_tools.run("click_target", {"target": "Save"})

        assert not ok.called
        assert "no match" in warn.call_args[0][0]

    def test_a_logging_failure_never_changes_the_action_result(self):
        """The observer must not be able to break the backend."""
        with patch.object(
            direct_tools, "_dispatch", return_value={"ok": True, "result": "done"}
        ), patch("jarvis.utils.logging.ok", side_effect=RuntimeError("stream closed")):
            result = direct_tools.run("system_status", {})

        assert result["ok"] is True and result["result"] == "done"

    def test_long_arguments_stay_on_one_readable_line(self):
        typed = "x" * 400
        with patch.object(
            direct_tools, "_dispatch", return_value={"ok": True, "result": "typed"}
        ), patch("jarvis.utils.logging.ok") as ok:
            direct_tools.run("type_into", {"target": "Search", "text": typed})

        message = ok.call_args[0][0]
        assert "\n" not in message
        assert typed not in message
        assert "Search" in message

    def test_announcement_never_replaces_the_dispatched_result(self):
        """run() is a wrapper: every key the transports rely on passes through."""
        dispatched = {"ok": True, "result": "15", "state": "running"}
        with patch.object(direct_tools, "_dispatch", return_value=dispatched), patch(
            "jarvis.utils.logging.ok"
        ):
            assert direct_tools.run("system_status", {}) == dispatched


def test_live_config_serves_the_direct_tool_names():
    """The page registers one client-tool handler per name it is given."""
    from jarvis.config import Config
    from jarvis.browser import BrowserRequestHandler

    handler = object.__new__(BrowserRequestHandler)
    handler._require_api_access = lambda require_origin=False: True
    responses: list = []
    handler._json = lambda status, body: responses.append((status, body))

    with patch("jarvis.config.load_config", return_value=Config()):
        handler._handle_live_config()

    payload = responses[0][1]
    assert payload["direct_tools"] == [tool["name"] for tool in direct_tools.declarations()]
    assert "run_command" in payload["direct_tools"]
