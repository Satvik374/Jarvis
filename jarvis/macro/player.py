"""Macro Player for Jarvis.

Executes synthesized Macros with precise speed control, window focus handling,
and dynamic parameter substitution.
"""

from __future__ import annotations

import re
import subprocess
import threading
import time
from typing import Any, Dict, Optional, Union

from .manager import Macro, MacroStep, get_macro_manager
from ..utils import logging as log


class MacroPlayer:
    """Replays Macro steps safely with speed and parameter control."""

    def __init__(self, macro_manager=None):
        self.mgr = macro_manager or get_macro_manager()

    def play(
        self,
        macro_or_name: Union[Macro, str],
        speed: float = 1.0,
        params: Optional[Dict[str, Any]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> Dict[str, Any]:
        """Execute all steps of a Macro.

        Args:
            macro_or_name: Macro object or name string.
            speed: Speed multiplier (>0.0, e.g. 1.0 = normal, 2.0 = 2x speed).
            params: Dictionary of parameter values to replace in text/arguments.
            cancel_event: Threading event to gracefully abort playback.

        Returns:
            Dict containing execution results.
        """
        if isinstance(macro_or_name, str):
            macro = self.mgr.load_macro(macro_or_name)
            if not macro:
                return {
                    "ok": False,
                    "message": f"Macro '{macro_or_name}' not found.",
                    "steps_executed": 0,
                }
        else:
            macro = macro_or_name

        if not macro.steps:
            return {
                "ok": True,
                "message": f"Macro '{macro.name}' has no steps.",
                "steps_executed": 0,
            }

        speed_factor = max(0.1, float(speed))
        params_dict = params or {}
        cancel = cancel_event or threading.Event()

        log.info(f"▶ Playing Macro: '{macro.name}' ({len(macro.steps)} steps, speed: {speed_factor:.1f}x)")
        executed = 0

        try:
            import pyautogui
            # Disable pyautogui fail-safe delay for snappy playback
            pyautogui.PAUSE = 0.05 / speed_factor
        except Exception:
            pyautogui = None

        for idx, step in enumerate(macro.steps, start=1):
            if cancel.is_set():
                log.warn(f"Macro '{macro.name}' playback interrupted by user at step {idx}.")
                return {
                    "ok": False,
                    "message": f"Playback interrupted at step {idx}/{len(macro.steps)}.",
                    "steps_executed": executed,
                }

            self._execute_step(step, speed_factor, params_dict, pyautogui)
            executed += 1

            # Inter-step delay
            step_delay = max(0.05, step.delay / speed_factor)
            time.sleep(step_delay)

        log.ok(f"✓ Macro '{macro.name}' completed successfully ({executed} steps).")
        return {
            "ok": True,
            "message": f"Macro '{macro.name}' finished successfully.",
            "steps_executed": executed,
            "macro": macro.name,
        }

    def _execute_step(
        self,
        step: MacroStep,
        speed: float,
        params: Dict[str, Any],
        pyautogui: Any,
    ) -> None:
        action = step.action.lower()
        args = step.args.copy()

        # Parameter substitution for strings
        for k, v in list(args.items()):
            if isinstance(v, str):
                for p_key, p_val in params.items():
                    args[k] = args[k].replace(f"{{{p_key}}}", str(p_val))

        if action == "focus_window":
            win_title = args.get("title", "")
            if win_title:
                self._focus_window_by_title(win_title)

        elif action in {"click", "left_click"}:
            x, y = args.get("x"), args.get("y")
            if x is not None and y is not None and pyautogui:
                pyautogui.click(x=x, y=y)

        elif action == "double_click":
            x, y = args.get("x"), args.get("y")
            if x is not None and y is not None and pyautogui:
                pyautogui.doubleClick(x=x, y=y)

        elif action == "right_click":
            x, y = args.get("x"), args.get("y")
            if x is not None and y is not None and pyautogui:
                pyautogui.rightClick(x=x, y=y)

        elif action == "type":
            text = args.get("text", "")
            if text and pyautogui:
                pyautogui.write(text, interval=0.01 / speed)

        elif action == "press":
            keys = args.get("keys", "")
            if keys and pyautogui:
                if "+" in keys:
                    parts = [k.strip().lower() for k in keys.split("+")]
                    pyautogui.hotkey(*parts)
                else:
                    pyautogui.press(keys.strip().lower())

        elif action == "launch":
            cmd = args.get("command", "")
            if cmd:
                subprocess.Popen(cmd, shell=True)
                time.sleep(1.0 / speed)

        elif action == "wait":
            sec = float(args.get("seconds", step.delay))
            time.sleep(sec / speed)

    def _focus_window_by_title(self, partial_title: str) -> bool:
        """Bring window matching title to foreground."""
        try:
            import win32gui
            import win32con

            found_hwnd = None

            def _enum_handler(hwnd, _):
                nonlocal found_hwnd
                if win32gui.IsWindowVisible(hwnd):
                    title = win32gui.GetWindowText(hwnd)
                    if partial_title.lower() in title.lower():
                        found_hwnd = hwnd

            win32gui.EnumWindows(_enum_handler, None)
            if found_hwnd:
                win32gui.ShowWindow(found_hwnd, win32con.SW_RESTORE)
                win32gui.SetForegroundWindow(found_hwnd)
                time.sleep(0.2)
                return True
        except Exception:
            pass
        return False
