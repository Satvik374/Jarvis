"""Unit tests for Shadow Desktop & Virtual Workspace Execution (Ghost Automation)."""

import unittest
from unittest.mock import Mock, patch, MagicMock
from PIL import Image

from jarvis.config import Config, ShadowConfig, load_config
from jarvis.desktop import (
    ShadowDesktopManager,
    ShadowWindow,
    get_shadow_manager,
    is_shadow_enabled,
    toggle_shadow,
    set_shadow_enabled,
    VirtualInputDispatcher,
    get_virtual_input,
    ShadowCaptureEngine,
    get_shadow_capture,
)
from jarvis.tools import apps
from jarvis.tools import registry
from jarvis.perception import screen, elements
from jarvis.hud.controller import HudController
from jarvis.console import _command


class ShadowDesktopTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Config()
        set_shadow_enabled(False)

    def tearDown(self):
        set_shadow_enabled(False)

    def test_01_shadow_config_and_defaults(self):
        """Test ShadowConfig dataclass default fields and Config integration."""
        shadow_cfg = ShadowConfig()
        self.assertFalse(shadow_cfg.enabled)
        self.assertEqual(shadow_cfg.desktop_name, "JarvisShadowDesktop")
        self.assertTrue(shadow_cfg.headless)
        self.assertTrue(shadow_cfg.pip_stream)
        self.assertEqual(shadow_cfg.resolution, (1920, 1080))

        cfg = Config()
        self.assertIsInstance(cfg.shadow, ShadowConfig)
        self.assertFalse(cfg.shadow.enabled)

    def test_02_shadow_manager_toggle_and_lifecycle(self):
        """Test enabling, disabling, and toggling shadow mode."""
        mgr = get_shadow_manager()
        self.assertFalse(mgr.is_enabled())

        mgr.enable()
        self.assertTrue(mgr.is_enabled())
        self.assertTrue(is_shadow_enabled())

        mgr.disable()
        self.assertFalse(mgr.is_enabled())
        self.assertFalse(is_shadow_enabled())

        state = toggle_shadow()
        self.assertTrue(state)
        self.assertTrue(is_shadow_enabled())

        state = toggle_shadow()
        self.assertFalse(state)
        self.assertFalse(is_shadow_enabled())

    def test_03_shadow_window_dataclass(self):
        """Test ShadowWindow geometry calculation and properties."""
        win = ShadowWindow(
            hwnd=12345,
            title="Google Chrome - Shadow",
            class_name="Chrome_WidgetWin_1",
            rect=(100, 200, 900, 800),
            is_visible=True
        )
        self.assertEqual(win.hwnd, 12345)
        self.assertEqual(win.width, 800)
        self.assertEqual(win.height, 600)
        self.assertEqual(win.center, (500, 500))

    def test_04_virtual_input_dispatcher(self):
        """Test VirtualInputDispatcher mouse and keyboard event dispatch."""
        dispatcher = VirtualInputDispatcher()

        with patch.object(dispatcher, "_is_windows", True):
            mock_user32 = MagicMock()
            dispatcher._user32 = mock_user32

            # Test synthetic click
            ok = dispatcher.click(x=350, y=450, hwnd=9999, button="left", clicks=1)
            self.assertTrue(ok)
            self.assertTrue(mock_user32.PostMessageW.called)

            # Test text typing
            mock_user32.reset_mock()
            ok_type = dispatcher.type_text("Hello Jarvis", hwnd=9999)
            self.assertTrue(ok_type)
            self.assertEqual(mock_user32.PostMessageW.call_count, len("Hello Jarvis") + 1)

            # Test key press
            mock_user32.reset_mock()
            ok_key = dispatcher.press_key("enter", hwnd=9999)
            self.assertTrue(ok_key)
            self.assertTrue(mock_user32.PostMessageW.called)

            # Test scroll
            mock_user32.reset_mock()
            ok_scroll = dispatcher.scroll(clicks=3, x=100, y=100, hwnd=9999)
            self.assertTrue(ok_scroll)
            self.assertTrue(mock_user32.PostMessageW.called)

    def test_05_shadow_capture_engine(self):
        """Test ShadowCaptureEngine frame generation."""
        engine = ShadowCaptureEngine(default_resolution=(1280, 720))

        # Test background canvas capture when no windows exist
        with patch.object(get_shadow_manager(), "list_windows", return_value=[]):
            img = engine.capture_shadow_desktop()
            self.assertIsInstance(img, Image.Image)
            self.assertEqual(img.size, (1280, 720))

        # Test window capture fallback
        with patch.object(engine, "_is_windows", False):
            res = engine.capture_window(1234)
            self.assertIsNone(res)

    def test_06_screen_and_elements_perception_routing(self):
        """Test that perception routines query shadow desktop when shadow mode is active."""
        set_shadow_enabled(True)

        # 1. Screen capture
        shot = screen.capture()
        self.assertIsInstance(shot.image, Image.Image)

        # 2. Elements active window title
        mgr = get_shadow_manager()
        mock_win = ShadowWindow(hwnd=1001, title="Notepad Shadow", class_name="Notepad", rect=(0, 0, 800, 600), is_visible=True)
        with patch.object(mgr, "list_windows", return_value=[mock_win]):
            title = elements._active_window_title()
            self.assertEqual(title, "Notepad Shadow")

    def test_07_apps_and_tools_shadow_routing(self):
        """Test that open_app and tool actions route through shadow manager when enabled."""
        set_shadow_enabled(True)
        mgr = get_shadow_manager()

        with patch.object(mgr, "spawn_process") as mock_spawn:
            res = apps.open_app("notepad")
            self.assertIn("Shadow Workspace", res)
            mock_spawn.assert_called_once()

        # Test tool registry click handler
        obs_mock = Mock(elements=[], active_window="Shadow Window", screen_size=(1920, 1080))
        with patch.object(get_virtual_input(), "click", return_value=True) as mock_click:
            click_res = registry.execute("click", {"x": 200, "y": 300}, obs_mock, self.cfg)
            self.assertTrue(click_res.ok)
            self.assertIn("shadow workspace", click_res.message)
            mock_click.assert_called_once()

        # Test tool registry type handler
        with patch.object(get_virtual_input(), "type_text", return_value=True) as mock_type:
            type_res = registry.execute("type", {"text": "Ghost typing test"}, obs_mock, self.cfg)
            self.assertTrue(type_res.ok)
            self.assertIn("shadow workspace", type_res.message)
            mock_type.assert_called_once()

    def test_08_hud_and_console_shadow_controls(self):
        """Test HUD and Console :shadow command toggle and control."""
        # 1. HUD toggle
        hud_controller = HudController(cfg=self.cfg)
        self.assertFalse(is_shadow_enabled())
        new_st = hud_controller.toggle_shadow()
        self.assertTrue(new_st)
        self.assertTrue(is_shadow_enabled())
        self.assertIn("Shadow Desktop ON", hud_controller.detail_text)

        # 2. Console :shadow commands
        _command(":shadow off", self.cfg)
        self.assertFalse(is_shadow_enabled())

        _command(":shadow on", self.cfg)
        self.assertTrue(is_shadow_enabled())

        _command(":shadow toggle", self.cfg)
        self.assertFalse(is_shadow_enabled())

        _command(":shadow status", self.cfg)


if __name__ == "__main__":
    unittest.main()
