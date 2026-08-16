import pytest
from unittest.mock import MagicMock, patch

from jarvis.config import Config
from jarvis.tools import apps, registry
from jarvis.perception import elements
from jarvis.perception.elements import Observation, Element


class DummyWindow:
    def __init__(self, title, is_minimized=False, is_maximized=False):
        self.title = title
        self.isMinimized = is_minimized
        self.isMaximized = is_maximized
        self.x = 100
        self.y = 100
        self.width = 800
        self.height = 600
        self._maximized = False
        self._minimized = False

    def maximize(self):
        self._maximized = True

    def minimize(self):
        self._minimized = True

    def restore(self):
        self.isMinimized = False
        self.isMaximized = False

    def moveTo(self, x, y):
        self.x = x
        self.y = y

    def resizeTo(self, width, height):
        self.width = width
        self.height = height


def test_snap_window_left(monkeypatch):
    dummy = DummyWindow("Editor")
    monkeypatch.setattr("pygetwindow.getActiveWindow", lambda: dummy)
    monkeypatch.setattr("pygetwindow.getAllWindows", lambda: [dummy])
    monkeypatch.setattr("jarvis.perception.screen.screen_size", lambda: (1920, 1080))

    res = apps.snap_window("left")
    assert "snapped 'Editor' to left half" in res
    assert dummy.x == 0
    assert dummy.y == 0
    assert dummy.width == 960
    assert dummy.height == 1080


def test_snap_window_right(monkeypatch):
    dummy = DummyWindow("Browser")
    monkeypatch.setattr("pygetwindow.getActiveWindow", lambda: dummy)
    monkeypatch.setattr("pygetwindow.getAllWindows", lambda: [dummy])
    monkeypatch.setattr("jarvis.perception.screen.screen_size", lambda: (1920, 1080))

    res = apps.snap_window("right")
    assert "snapped 'Browser' to right half" in res
    assert dummy.x == 960
    assert dummy.y == 0
    assert dummy.width == 960
    assert dummy.height == 1080


def test_snap_window_maximize(monkeypatch):
    dummy = DummyWindow("Terminal")
    monkeypatch.setattr("pygetwindow.getActiveWindow", lambda: dummy)
    monkeypatch.setattr("pygetwindow.getAllWindows", lambda: [dummy])

    res = apps.snap_window("maximize")
    assert "maximized 'Terminal'" in res
    assert dummy._maximized is True


def test_tile_windows_side_by_side(monkeypatch):
    w1 = DummyWindow("Window 1")
    w2 = DummyWindow("Window 2")
    monkeypatch.setattr("pygetwindow.getAllWindows", lambda: [w1, w2])
    monkeypatch.setattr("jarvis.perception.screen.screen_size", lambda: (1920, 1080))
    monkeypatch.setattr(apps, "_is_own_console", lambda w: False)

    res = apps.tile_windows("side_by_side")
    assert "tiled 2 windows side-by-side" in res
    assert w1.x == 0
    assert w1.width == 960
    assert w2.x == 960
    assert w2.width == 960


def test_window_actions_in_registry(monkeypatch):
    dummy = DummyWindow("App")
    monkeypatch.setattr("pygetwindow.getActiveWindow", lambda: dummy)
    monkeypatch.setattr("pygetwindow.getAllWindows", lambda: [dummy])
    monkeypatch.setattr("jarvis.perception.screen.screen_size", lambda: (1920, 1080))

    cfg = Config()
    obs = Observation(elements=[], screen_size=(1920, 1080))

    res_snap = registry.execute("snap_window", {"direction": "left"}, obs, cfg)
    assert res_snap.ok is True
    assert "snapped" in res_snap.message

    res_tile = registry.execute("tile_windows", {"layout": "side_by_side"}, obs, cfg)
    assert res_tile.ok is True
    assert "tiled" in res_tile.message


def test_ocr_fallback_when_uia_empty(monkeypatch):
    monkeypatch.setattr(elements, "_detect_uia", lambda max_e, size, window_title="": [])
    monkeypatch.setattr(elements, "_active_window_title", lambda: "Canvas App")
    monkeypatch.setattr(
        elements,
        "_detect_ocr",
        lambda max_e: [Element(id=0, role="Text", name="Play", bbox=(10, 10, 50, 30), center=(30, 20))]
    )

    obs = elements.observe(max_elements=10, use_uia=True, use_ocr=True)
    assert len(obs.elements) == 1
    assert obs.elements[0].name == "Play"
