"""Regression cases from the bug audit; no real input, network, or user data."""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PIL import Image

from jarvis.agent import loop
from jarvis.agent.prompts import parse_decision
from jarvis.config import Config
from jarvis.perception import screen
from jarvis.perception.elements import Observation
from jarvis.tools import files, keyboard, mouse, registry


@pytest.mark.parametrize("coordinates", [[], [42]])
def test_short_coordinate_array_does_not_crash_action_parser(coordinates):
    decision = parse_decision(json.dumps({"action": "click", "args": {
        "coordinate": coordinates}}))
    assert decision.action == "click"
    assert "x" not in decision.args and "y" not in decision.args


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None, [], {}])
def test_verifier_requires_an_actual_json_boolean(value):
    agent = object.__new__(loop.Agent)
    agent.cfg = Config()
    agent.brain = Mock()
    agent.brain.complete.return_value = json.dumps({"success": value})
    verdict, reason = agent._verify_success(
        "test", [], obs=Observation([], (100, 100)), image=object())
    assert verdict is None, "malformed verdicts must never verify a task"
    assert "verdict" in reason


@pytest.mark.parametrize("value", [False, True])
def test_verifier_preserves_valid_boolean_verdicts(value):
    agent = object.__new__(loop.Agent)
    agent.cfg = Config()
    agent.brain = Mock()
    agent.brain.complete.return_value = json.dumps({"success": value})
    verdict, _ = agent._verify_success(
        "test", [], obs=Observation([], (100, 100)), image=object())
    assert verdict is value


def test_missing_confirmation_input_is_not_permission_to_act():
    agent = object.__new__(loop.Agent)
    agent.cfg = Config()
    agent.cfg.safety.confirm_each_action = True
    decision = SimpleNamespace(action="delete_file", args={"path": "test.txt"})
    with patch("builtins.input", side_effect=EOFError):
        assert agent._confirm(decision) is False


def test_keyboard_sequence_stops_and_reports_a_refused_key():
    with patch.object(keyboard, "press", side_effect=["pressed ctrl+s", "refused: protected window"]) as press:
        message = keyboard.press_sequence(["ctrl+s", "alt+f4", "enter"])
    assert message.startswith("refused:")
    assert press.call_count == 2


@pytest.mark.parametrize("action,tool,result", [
    ("press", "keyboard.press", "refused: protected window"),
    ("key_sequence", "keyboard.press_sequence", "refused: protected window"),
    ("open_app", "apps.open_app", "could not launch 'missing': error"),
    ("focus_window", "apps.focus_window", "no window matching 'missing'"),
    ("snap_window", "apps.snap_window", "no active window found to snap"),
    ("tile_windows", "apps.tile_windows", "window manager unavailable: error"),
    ("open_url", "system.open_url", "could not open https://example.test"),
])
def test_failed_desktop_tools_are_not_reported_as_success(action, tool, result):
    args = {"keys": ["enter"] if action == "key_sequence" else "enter",
            "name": "missing", "url": "https://example.test"}
    with (patch("jarvis.desktop.is_shadow_enabled", return_value=False),
          patch("jarvis.tools.registry." + tool, return_value=result)):
        response = registry.execute(action, args, Observation([], (100, 100)), Config())
    assert not response.ok
    assert response.message == result


def test_drag_releases_button_when_motion_fails():
    pg = Mock()
    pg.size.return_value = (1920, 1080)
    pg.moveTo.side_effect = [None, RuntimeError("motion failed")]
    with (patch.object(mouse, "_pg", return_value=pg),
          patch.object(mouse.time, "sleep")):
        with pytest.raises(RuntimeError, match="motion failed"):
            mouse.drag(10, 10, 20, 20)
    pg.mouseUp.assert_called_once()


def hud():
    controller = Mock()
    controller.overlay._running = True
    controller.is_hud_visible.return_value = True
    return controller


def test_capture_context_restores_hud_and_preserves_body_exception():
    controller = hud()
    with patch("jarvis.hud.get_hud_controller", return_value=controller):
        with pytest.raises(ValueError, match="original failure"):
            with screen._hide_hud_for_capture():
                raise ValueError("original failure")
    controller.show_hud_sync.assert_called_once()


def test_hud_restore_failure_does_not_break_a_successful_capture():
    controller = hud()
    controller.show_hud_sync.side_effect = RuntimeError("HUD unavailable")
    with patch("jarvis.hud.get_hud_controller", return_value=controller):
        with screen._hide_hud_for_capture():
            pass


def test_pillow_fallback_captures_before_hud_is_restored():
    controller = hud()
    mss = SimpleNamespace(MSS=Mock(side_effect=RuntimeError("MSS unavailable")))

    def grab():
        controller.hide_hud_sync.assert_called_once()
        controller.show_hud_sync.assert_not_called()
        return Image.new("RGB", (20, 10))

    with (patch("jarvis.desktop.is_shadow_enabled", return_value=False),
          patch("jarvis.hud.get_hud_controller", return_value=controller),
          patch.dict(sys.modules, {"mss": mss}),
          patch("PIL.ImageGrab.grab", side_effect=grab) as capture):
        shot = screen.capture()
    capture.assert_called_once()
    assert (shot.width, shot.height) == (20, 10)
    controller.show_hud_sync.assert_called_once()


@pytest.mark.parametrize("network_error", [False, True])
def test_download_never_overwrites_or_deletes_a_file_created_during_request(tmp_path, network_error):
    dest = tmp_path / "output.bin"
    response = Mock()
    response.status_code = 200
    response.iter_content.return_value = [b"downloaded data"]
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)

    def request(*args, **kwargs):
        dest.write_bytes(b"other writer's data")
        if network_error:
            raise RuntimeError("network failed before opening file")
        return response

    with patch("requests.get", side_effect=request):
        message = files.download_file("https://example.test/file", str(dest), allow=(str(tmp_path),))
    assert not message.startswith("downloaded")
    assert dest.read_bytes() == b"other writer's data"


def test_failed_download_removes_its_own_partial_file(tmp_path):
    dest = tmp_path / "output.bin"
    response = Mock()
    response.status_code = 200
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)

    def chunks(**kwargs):
        yield b"partial"
        raise RuntimeError("connection dropped")

    response.iter_content.side_effect = chunks
    with patch("requests.get", return_value=response):
        message = files.download_file("https://example.test/file", str(dest), allow=(str(tmp_path),))
    assert message.startswith("could not download")
    assert not dest.exists()
