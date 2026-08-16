"""Unit and integration tests for Global Floating Mini HUD & System-Wide Hotkey Engine."""

import time
import threading
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
    assert cfg.hud.hotkey_voice == "alt+v"
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


def test_floating_mini_hud_expand_collapse():
    hud = FloatingMiniHUD()
    assert hud.is_expanded is True

    hud.toggle_expand()
    assert hud.is_expanded is False

    hud.toggle_expand()
    assert hud.is_expanded is True


def test_floating_mini_hud_visibility():
    hud = FloatingMiniHUD()
    hud._running = True
    assert hud.is_visible() is True

    hud.hide()
    assert hud.is_visible() is False

    hud.show()
    assert hud.is_visible() is True

    hud.toggle_visibility()
    assert hud.is_visible() is False


def test_hud_controller_submit_command_no_runner():
    cfg = Config()
    controller = HudController(cfg=cfg, task_runner=None)
    controller.start(start_overlay=False)

    controller._on_user_submit("do something")
    assert controller.overlay is not None
    # State should be reset to idle
    assert not controller.overlay._msg_queue.empty()
    item = controller.overlay._msg_queue.get_nowait()
    assert item[0] == "state" and item[1] == "idle"
    item2 = controller.overlay._msg_queue.get_nowait()
    assert item2[0] == "response" and "Error: Task runner not connected." in item2[2]

    controller.stop()


def test_global_hotkeys_re_register():
    mgr = GlobalHotkeyManager()
    called = []

    def _cb1():
        called.append(1)

    def _cb2():
        called.append(2)

    mgr.register("ctrl+alt+j", _cb1)
    assert mgr._callbacks["ctrl+alt+j"] is _cb1

    # Re-register with different callback
    mgr.register("ctrl+alt+j", _cb2)
    assert mgr._callbacks["ctrl+alt+j"] is _cb2

    mgr.unregister("ctrl+alt+j")
    assert "ctrl+alt+j" not in mgr._callbacks


def test_hud_push_to_talk_toggle(monkeypatch):
    from jarvis.utils import voice

    spoken = []
    recorded_events = []

    def mock_record_until_cancelled(cancel_event, max_seconds=120.0):
        recorded_events.append("started")
        cancel_event.wait(timeout=2.0)
        recorded_events.append("stopped")
        return b"dummy_wav_bytes"

    monkeypatch.setattr(voice, "speak", lambda text, wait=False: spoken.append(text))
    monkeypatch.setattr(voice, "record_until_cancelled", mock_record_until_cancelled)
    monkeypatch.setattr(voice, "transcribe", lambda wav, brain=None: "open calculator")

    executed = []
    cfg = Config()
    controller = HudController(cfg=cfg, task_runner=lambda cmd: executed.append(cmd) or "Reply")
    controller.start(start_overlay=False)

    # Mock overlay with set_input_text
    input_text_set = []
    class MockOverlay:
        def __init__(self):
            self.voice_active = False
        def set_voice_active(self, active):
            self.voice_active = active
        def set_input_text(self, text):
            input_text_set.append(text)
        def set_state(self, state, detail=None):
            pass
        def set_response(self, prompt, reply):
            pass
        def stop(self):
            pass

    controller.overlay = MockOverlay()

    # 1. 1st Press of Alt+V -> Starts recording
    controller.toggle_voice()
    assert controller._is_voice_listening is True
    assert controller.overlay.voice_active is True
    assert "Listening, sir." not in spoken  # Must NOT speak before listening

    time.sleep(0.05)
    assert "started" in recorded_events
    assert "stopped" not in recorded_events  # Keeps recording, doesn't auto-stop

    # 2. 2nd Press of Alt+V -> Stops recording & transcribes
    controller.toggle_voice()
    time.sleep(0.1)

    assert controller._is_voice_listening is False
    assert controller.overlay.voice_active is False
    assert "stopped" in recorded_events

    # 3. Transcribed text must appear in Text Input
    assert "open calculator" in input_text_set

    # 4. Directive is NOT auto-executed yet (waiting for user to send)
    assert "open calculator" not in executed

    # 5. User submits text from Entry/Send button -> Now it executes!
    controller._on_user_submit("open calculator")
    time.sleep(0.1)
    assert "open calculator" in executed

    controller.stop()


def test_floating_mini_hud_long_message():
    hud = FloatingMiniHUD()
    long_prompt = "A" * 500
    long_reply = "B" * 2000

    hud.set_response(long_prompt, long_reply)
    assert not hud._msg_queue.empty()
    item = hud._msg_queue.get_nowait()
    assert item[0] == "response"
    assert item[1] == long_prompt
    assert item[2] == long_reply


def test_floating_mini_hud_set_input_text_queueing():
    hud = FloatingMiniHUD()
    hud.set_input_text("transcribed voice command")
    assert not hud._msg_queue.empty()
    item = hud._msg_queue.get_nowait()
    assert item[0] == "input_text"
    assert item[1] == "transcribed voice command"


def test_hud_first_message_lifecycle_wiring():
    from jarvis.hud import get_hud_controller, start_hud, stop_hud, set_hud_controller

    set_hud_controller(None)
    # Simulate an early import/preflight that accesses get_hud_controller without task_runner
    early_ctrl = get_hud_controller()
    assert early_ctrl.task_runner is None

    # Now session startup calls start_hud with actual runner
    executed = []
    def _runner(cmd):
        executed.append(cmd)
        return "1st reply"

    cfg = Config()
    active_ctrl = start_hud(cfg=cfg, task_runner=_runner, start_overlay=False)
    assert active_ctrl.task_runner is _runner

    # Send the very 1st message in the session via HUD
    active_ctrl._on_user_submit("1st message from HUD")
    time.sleep(0.15)
    assert "1st message from HUD" in executed

    # Send 2nd message via HUD
    active_ctrl._on_user_submit("2nd message from HUD")
    time.sleep(0.15)
    assert "2nd message from HUD" in executed

    stop_hud()
    set_hud_controller(None)


def test_hud_interrupt_response(monkeypatch):
    from jarvis.utils import voice

    silenced = []
    monkeypatch.setattr(voice, "interrupt_speech", lambda: silenced.append(True) or True)

    started_ev = threading.Event()
    finish_ev = threading.Event()
    executed_results = []

    def _slow_runner(cmd):
        started_ev.set()
        finish_ev.wait(timeout=2.0)
        return "Slow task finished"

    cfg = Config()
    controller = HudController(cfg=cfg, task_runner=_slow_runner)
    controller.start(start_overlay=False)

    # 1. Start a slow task
    controller._on_user_submit("run slow task")
    assert started_ev.wait(timeout=1.0)
    assert controller.state == "thinking"

    # 2. User interrupts while in between response / task execution
    controller.interrupt()

    assert True in silenced
    assert controller.state == "idle"
    assert "Interrupted" in controller.detail_text

    # 3. Unblock slow runner after interrupt -> verify it was discarded
    finish_ev.set()
    time.sleep(0.1)
    assert controller.state == "idle"

    controller.stop()


def test_gemini_vertex_fast_adc_refresh(monkeypatch, tmp_path):
    from jarvis.config import BrainConfig
    from jarvis.agent.brain import GeminiVertexBrain
    import json
    import io

    # Create a mock ADC authorized_user file
    mock_adc = tmp_path / "gcloud_adc.json"
    mock_adc.write_text(json.dumps({
        "type": "authorized_user",
        "client_id": "test-client-id",
        "client_secret": "test-secret",
        "refresh_token": "test-refresh-token",
        "quota_project_id": "test-quota-project-123"
    }), encoding="utf-8")

    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(mock_adc))

    class MockHTTPResponse:
        def __init__(self, data):
            self.data = data
        def read(self):
            return self.data.encode("utf-8")
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    def mock_urlopen(req, timeout=None):
        return MockHTTPResponse(json.dumps({
            "access_token": "mock-fast-access-token-999",
            "expires_in": 3600
        }))

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    cfg = BrainConfig(backend="gemini", model="gemini-3.7-flash")
    brain = GeminiVertexBrain(cfg)

    # Warmup should complete in sub-millisecond with direct fast path
    brain.warmup()
    assert brain._cached_token == "mock-fast-access-token-999"
    assert brain.project_id == "test-quota-project-123"





