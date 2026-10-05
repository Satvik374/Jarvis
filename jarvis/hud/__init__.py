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
    agent: Optional[Any] = None,
) -> HudController:
    global _CONTROLLER
    if _CONTROLLER is None:
        _CONTROLLER = HudController(cfg=cfg, task_runner=task_runner, agent=agent)
    else:
        if cfg is not None:
            _CONTROLLER.cfg = cfg
            _CONTROLLER.hud_cfg = getattr(cfg, "hud", None)
        if task_runner is not None:
            _CONTROLLER.task_runner = task_runner
        if agent is not None:
            _CONTROLLER.agent = agent
    return _CONTROLLER


def set_hud_controller(controller: Optional[HudController]) -> None:
    global _CONTROLLER
    _CONTROLLER = controller


def hud_overlay():
    """The live HUD overlay, or None when the HUD is not running.

    Read-only, unlike :func:`get_hud_controller`: the tool-layer choreography
    asks whether anything is watching on the path of *every* action, and must
    not spin up a controller (or its tkinter window) to find out.
    """
    controller = _CONTROLLER
    if controller is None:
        return None
    return getattr(controller, "overlay", None)


def start_hud(
    cfg: Optional[Config] = None,
    task_runner: Optional[Callable[[str], None]] = None,
    start_overlay: bool = True,
    agent: Optional[Any] = None,
) -> HudController:
    controller = get_hud_controller(cfg=cfg, task_runner=task_runner, agent=agent)
    controller.start(start_overlay=start_overlay, agent=agent)
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
    "hud_overlay",
    "start_hud",
    "stop_hud",
    "toggle_hud",
]
