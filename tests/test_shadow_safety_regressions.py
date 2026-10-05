"""Shadow failures must never operate on or read the primary desktop."""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import pytest

from jarvis import desktop
from jarvis.config import Config
from jarvis.desktop.manager import ShadowDesktopManager
from jarvis.desktop.virtual_input import VirtualInputDispatcher, WM_CHAR
from jarvis.perception import _comtypes_fix, elements, screen
from jarvis.tools import apps, registry, system


@pytest.fixture
def shadow(monkeypatch):
    monkeypatch.setattr(desktop, "is_shadow_enabled", lambda: True)
    dispatcher = Mock()
    monkeypatch.setattr(desktop, "get_virtual_input", lambda: dispatcher)
    return dispatcher


INPUT_CASES = [
    ("click", {"x": 100, "y": 100}, "click", "mouse.click"),
    ("double_click", {"x": 100, "y": 100}, "click", "mouse.double_click"),
    ("triple_click", {"x": 100, "y": 100}, "click", "mouse.triple_click"),
    ("right_click", {"x": 100, "y": 100}, "click", "mouse.right_click"),
    ("type", {"text": "test"}, "type_text", "keyboard.type_text"),
    ("press", {"keys": "enter"}, "press_key", "keyboard.press"),
    ("scroll", {"dy": 3}, "scroll", "mouse.scroll"),
]


@pytest.mark.parametrize("action,args,method,host", INPUT_CASES)
@pytest.mark.parametrize("raises", [False, True])
def test_failed_virtual_input_never_falls_back_or_claims_success(shadow, action, args, method, host, raises):
    target = getattr(shadow, method)
    target.return_value = False
    if raises:
        target.side_effect = RuntimeError("shadow input unavailable")
    with patch("jarvis.tools.registry." + host) as physical:
        result = registry.execute(action, args, elements.Observation([], (1920, 1080)), Config())
    physical.assert_not_called()
    assert not result.ok
    assert "shadow" in result.message.lower()


def test_shadow_scroll_preserves_axes_and_downward_direction(shadow):
    shadow.scroll.return_value = True
    result = registry.execute("scroll", {"dy": 3, "dx": 2},
                              elements.Observation([], (1920, 1080)), Config())
    assert result.ok
    assert shadow.scroll.call_args_list == [call(-3), call(2, horizontal=True)]


def test_shadow_key_sequence_routes_every_key_and_stops_on_failure(shadow):
    shadow.press_key.side_effect = [True, False, True]
    with patch.object(registry.keyboard, "press_sequence") as physical:
        result = registry.execute("key_sequence", {"keys": ["tab", "enter", "escape"]},
                                  elements.Observation([], (1920, 1080)), Config())
    physical.assert_not_called()
    assert not result.ok
    assert shadow.press_key.call_args_list == [call("tab"), call("enter")]


@pytest.mark.parametrize("action,args,host", [
    ("move", {"x": 100, "y": 100}, "move"),
    ("drag", {"x1": 100, "y1": 100, "x2": 200, "y2": 200}, "drag"),
])
def test_unsupported_shadow_pointer_operations_fail_without_primary_input(shadow, action, args, host):
    with patch.object(registry.mouse, host) as physical:
        result = registry.execute(action, args, elements.Observation([], (1920, 1080)), Config())
    physical.assert_not_called()
    assert not result.ok
    assert "shadow" in result.message.lower()


@pytest.mark.parametrize("method,args", [("click", (10, 20)), ("type_text", ("a",)),
                                         ("press_key", ("enter",)), ("scroll", (3,))])
def test_dispatcher_detects_win32_rejected_messages(method, args):
    dispatcher = VirtualInputDispatcher()
    dispatcher._is_windows = True
    dispatcher._user32 = Mock()
    dispatcher._user32.PostMessageW.return_value = 0
    with patch("jarvis.desktop.virtual_input.time.sleep"):
        assert getattr(dispatcher, method)(*args, hwnd=9999) is False


def test_virtual_text_uses_utf16_units_for_non_bmp_characters():
    dispatcher = VirtualInputDispatcher()
    dispatcher._is_windows = True
    dispatcher._user32 = Mock()
    dispatcher._user32.PostMessageW.return_value = 1
    with patch("jarvis.desktop.virtual_input.time.sleep"):
        assert dispatcher.type_text("A\U0001f600", hwnd=9999)
    chars = [c.args[2] for c in dispatcher._user32.PostMessageW.call_args_list
             if c.args[1] == WM_CHAR]
    assert chars == [0x41, 0xD83D, 0xDE00]


def test_virtual_horizontal_scroll_uses_horizontal_windows_message():
    dispatcher = VirtualInputDispatcher()
    dispatcher._is_windows = True
    dispatcher._user32 = Mock()
    dispatcher._user32.PostMessageW.return_value = 1
    assert dispatcher.scroll(2, hwnd=9999, horizontal=True)
    assert dispatcher._user32.PostMessageW.call_args.args[1:3] == (0x020E, 240 << 16)


def test_shadow_capture_error_never_captures_primary_desktop(shadow):
    capture = Mock()
    capture.capture_shadow_desktop.side_effect = RuntimeError("shadow capture failed")
    with (patch.object(desktop, "get_shadow_capture", return_value=capture),
          patch.dict(sys.modules, {"mss": Mock()}) as modules,
          patch("PIL.ImageGrab.grab") as primary):
        with pytest.raises(RuntimeError, match="shadow capture failed"):
            screen.capture()
        modules["mss"].MSS.assert_not_called()
    primary.assert_not_called()


def test_shadow_uia_error_never_uses_primary_foreground_window(shadow):
    manager = Mock()
    manager.list_windows.side_effect = RuntimeError("shadow enumeration failed")
    auto = Mock()
    with (patch.object(desktop, "get_shadow_manager", return_value=manager),
          patch.object(_comtypes_fix, "ensure"),
          patch.dict(sys.modules, {"uiautomation": auto})):
        with pytest.raises(RuntimeError, match="shadow enumeration failed"):
            elements._detect_uia(60, (1920, 1080))
    auto.GetForegroundControl.assert_not_called()


def test_shadow_title_error_never_reads_primary_title(shadow):
    manager = Mock()
    manager.list_windows.side_effect = RuntimeError("shadow enumeration failed")
    windows = Mock()
    with (patch.object(desktop, "get_shadow_manager", return_value=manager),
          patch.dict(sys.modules, {"pygetwindow": windows})):
        title = elements._active_window_title()
    windows.getActiveWindow.assert_not_called()
    assert "Shadow" in title


@pytest.mark.parametrize("failure", [None, RuntimeError("spawn failed")])
def test_shadow_app_launch_failure_never_launches_on_primary(shadow, failure):
    manager = Mock()
    manager.spawn_process.return_value = failure
    if isinstance(failure, Exception):
        manager.spawn_process.side_effect = failure
    with (patch.object(desktop, "get_shadow_manager", return_value=manager),
          patch.object(apps.app_index, "resolve", return_value=None),
          patch.object(apps, "_shadow_evidence", return_value=""),
          patch.object(apps, "focus_window", return_value="no match"),
          patch.object(apps.subprocess, "Popen") as primary,
          patch.object(apps.os, "startfile", create=True) as shell):
        message = apps.open_app("notepad")
    primary.assert_not_called()
    shell.assert_not_called()
    assert message.startswith("could not")


@pytest.mark.parametrize("failure", [None, RuntimeError("spawn failed")])
def test_shadow_url_failure_never_opens_primary_browser(shadow, failure):
    manager = Mock()
    manager.spawn_url.return_value = failure
    if isinstance(failure, Exception):
        manager.spawn_url.side_effect = failure
    with (patch.object(desktop, "get_shadow_manager", return_value=manager),
          patch.object(system.webbrowser, "open") as primary):
        message = system.open_url("https://example.test")
    primary.assert_not_called()
    assert message.startswith("could not")


@pytest.mark.parametrize("method,args", [("close_window", ("test",)),
                                         ("snap_window", ("maximize",)),
                                         ("tile_windows", ())])
def test_unsupported_shadow_window_operations_do_not_touch_primary(shadow, method, args):
    windows = Mock()
    with patch.dict(sys.modules, {"pygetwindow": windows}):
        message = getattr(apps, method)(*args)
    windows.getAllWindows.assert_not_called()
    windows.getActiveWindow.assert_not_called()
    assert message.startswith("refused:")


@pytest.mark.parametrize("method,args", [("focus_window", ("test",)), ("list_windows", ())])
def test_shadow_window_query_failure_never_falls_through(shadow, method, args):
    manager = Mock()
    manager.find_window.side_effect = RuntimeError("unavailable")
    manager.list_windows.side_effect = RuntimeError("unavailable")
    windows = Mock()
    windows.getAllWindows.return_value = []
    with (patch.object(desktop, "get_shadow_manager", return_value=manager),
          patch.dict(sys.modules, {"pygetwindow": windows})):
        getattr(apps, method)(*args)
    windows.getAllWindows.assert_not_called()


@pytest.mark.parametrize("failure", ["desktop", "process", "exception", "disabled", "platform"])
def test_shadow_process_manager_never_uses_standard_spawn_on_failure(failure):
    manager = ShadowDesktopManager(enabled=True)
    manager._is_windows = failure != "platform"
    manager.enabled = failure != "disabled"
    manager._kernel32 = Mock()
    manager._kernel32.CreateProcessW.return_value = 0
    if failure == "exception":
        manager._kernel32.CreateProcessW.side_effect = RuntimeError("Win32 error")
    with (patch.object(manager, "ensure_desktop", return_value=failure != "desktop"),
          patch("subprocess.Popen") as primary):
        result = manager.spawn_process("notepad.exe")
    primary.assert_not_called()
    assert result is None
