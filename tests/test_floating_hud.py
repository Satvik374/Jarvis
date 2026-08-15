"""Unit and integration tests for Global Floating Mini HUD & System-Wide Hotkey Engine."""

import time
import pytest

from jarvis.config import Config, HudConfig, load_config
from jarvis.hud.hotkeys import GlobalHotkeyManager
from jarvis.hud.mini_overlay import FloatingMiniHUD
from jarvis.hud.controller import HudController
from jarvis.tools.registry import execute


def test_hud_config_defaults():
    cfg = Config()
    assert hasattr(cfg, "hud")
    assert isinstance(cfg.hud, HudConfig)
    assert cfg.hud.enabled is True
    assert cfg.hud.hotkey_toggle == "ctrl+alt+j"
    assert cfg.hud.hotkey_voice == "ctrl+alt+v"
    assert cfg.hud.hotkey_vision == "ctrl+alt+s"
    assert cfg.hud.hotkey_macro == "ctrl+alt+r"
    assert cfg.hud.position == "bottom_right"


def test_global_hotkeys_registration():
    mgr = GlobalHotkeyManager()
    called = []

    def _cb():
        called.append(True)

    ok = mgr.register("ctrl+alt+j", _cb)
    assert ok is True
    assert "ctrl+alt+j" in mgr._callbacks

    unreg = mgr.unregister("ctrl+alt+j")
    assert unreg is True
    assert "ctrl+alt+j" not in mgr._callbacks


def test_floating_mini_hud_state_queueing():
    hud = FloatingMiniHUD()
    assert hud.state == "idle"
    assert hud._msg_queue.empty()

    hud.set_state("thinking", detail="Crunching data...")
    assert not hud._msg_queue.empty()
    item = hud._msg_queue.get_nowait()
    assert item == ("state", "thinking", "Crunching data...")

    hud.set_voice_active(True)
    item2 = hud._msg_queue.get_nowait()
    assert item2 == ("voice", True)

    hud.set_macro_recording(True)
    item3 = hud._msg_queue.get_nowait()
    assert item3 == ("macro", True)

    hud.set_response("hello", "Hello sir")
    item4 = hud._msg_queue.get_nowait()
    assert item4 == ("response", "hello", "Hello sir")


def test_hud_controller_lifecycle():
    cfg = Config()
    cfg.hud.enabled = True

    controller = HudController(cfg=cfg)
    assert controller._is_active is False

    controller.start(start_overlay=False)
    assert controller._is_active is True
    assert controller.overlay is not None

    # Test state updates
    controller.set_state("acting", detail="Moving window")
    assert controller.overlay.state == "idle" or not controller.overlay._msg_queue.empty()

    controller.stop()
    assert controller._is_active is False
    assert controller.overlay is None


def test_hud_controller_submit_command():
    executed = []

    def _mock_runner(cmd: str) -> str:
        executed.append(cmd)
        return "Done"

    cfg = Config()
    controller = HudController(cfg=cfg, task_runner=_mock_runner)
    controller.start(start_overlay=False)

    controller._on_user_submit("open notepad")
    time.sleep(0.1)

    assert "open notepad" in executed
    controller.stop()


def test_hud_control_tool_action():
    cfg = Config()

    # 1. Status action
    res_stat = execute("hud_control", {"action": "status"}, None, cfg)
    assert res_stat.ok is True
    assert "Floating Mini HUD Status" in res_stat.message

    # 2. Set state action
    res_set = execute("hud_control", {"action": "set_state", "state": "acting", "detail": "Running tests"}, None, cfg)
    assert res_set.ok is True
    assert "acting" in res_set.message

    # 3. Toggle action
    res_tog = execute("hud_control", {"action": "toggle"}, None, cfg)
    assert res_tog.ok is True

    # 4. Unknown action
    res_err = execute("hud_control", {"action": "invalid_action"}, None, cfg)
    assert res_err.ok is False


def test_hud_hide_during_screenshot():
    from jarvis.perception.screen import _hide_hud_for_capture, capture
    from jarvis.hud import get_hud_controller, set_hud_controller

    cfg = Config()
    ctrl = HudController(cfg=cfg)
    set_hud_controller(ctrl)

    with _hide_hud_for_capture():
        shot = capture()
        assert shot is not None
        assert shot.width > 0
        assert shot.height > 0

    set_hud_controller(None)
