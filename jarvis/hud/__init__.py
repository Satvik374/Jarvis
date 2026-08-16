"""Global Floating Mini HUD & System-Wide Hotkey Package."""

from __future__ import annotations

from typing import Callable, Optional

from ..config import Config
from .controller import HudController
from .hotkeys import GlobalHotkeyManager, get_hotkey_manager
from .mini_overlay import FloatingMiniHUD

_CONTROLLER: Optional[HudController] = None


def get_hud_controller(
    cfg: Optional[Config] = None,
    task_runner: Optional[Callable[[str], None]] = None,
) -> HudController:
    global _CONTROLLER
    if _CONTROLLER is None:
        _CONTROLLER = HudController(cfg=cfg, task_runner=task_runner)
    else:
        if cfg is not None:
            _CONTROLLER.cfg = cfg
            _CONTROLLER.hud_cfg = getattr(cfg, "hud", None)
        if task_runner is not None:
            _CONTROLLER.task_runner = task_runner
    return _CONTROLLER


def set_hud_controller(controller: Optional[HudController]) -> None:
    global _CONTROLLER
    _CONTROLLER = controller


def start_hud(
    cfg: Optional[Config] = None,
    task_runner: Optional[Callable[[str], None]] = None,
    start_overlay: bool = True,
) -> HudController:
    controller = get_hud_controller(cfg=cfg, task_runner=task_runner)
    controller.start(start_overlay=start_overlay)
    return controller


def stop_hud() -> None:
    global _CONTROLLER
    if _CONTROLLER is not None:
        _CONTROLLER.stop()


def toggle_hud() -> None:
    global _CONTROLLER
    if _CONTROLLER is not None:
        _CONTROLLER.toggle_hud()


__all__ = [
    "HudController",
    "FloatingMiniHUD",
    "GlobalHotkeyManager",
    "get_hud_controller",
    "set_hud_controller",
    "start_hud",
    "stop_hud",
    "toggle_hud",
]
