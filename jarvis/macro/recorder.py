"""Macro Recorder for Jarvis.

Captures mouse clicks, UI element inspections, keyboard typing, hotkeys,
and active window transitions, synthesizing them into clean, high-level Macro steps.
"""

from __future__ import annotations

import string
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .manager import Macro, MacroStep, get_macro_manager
from ..utils import logging as log


@dataclass
class RawEvent:
    """An unprocessed raw input or window event."""
    kind: str                         # "click", "key", "focus_window"
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class MacroRecorder:
    """Watches user actions and learns reusable workflow macros."""

    def __init__(self, macro_manager=None):
        self.mgr = macro_manager or get_macro_manager()
        self._is_recording = False
        self._stop_event = threading.Event()
        self._record_thread: Optional[threading.Thread] = None
        self._raw_events: List[RawEvent] = []
        self._events_lock = threading.Lock()
        self._current_name = "macro"
        self._current_desc = ""
        self._keyboard_unhook = None

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    def start_recording(self, name: str, description: str = "") -> None:
        """Begin watching and recording desktop interactions."""
        if self._is_recording:
            log.warn(f"Already recording macro '{self._current_name}'.")
            return

        self._current_name = name.strip()
        self._current_desc = description.strip()
        self._raw_events.clear()
        self._is_recording = True
        self._stop_event.clear()

        # Capture initial active window
        init_win = self._get_active_window_title()
        if init_win:
            self._add_event("focus_window", {"title": init_win})

        # Start keyboard hook
        try:
            import keyboard
            self._keyboard_unhook = keyboard.hook(self._on_key_event)
        except Exception as exc:
            log.warn(f"Keyboard hook unavailable ({exc}); keyboard recording will be limited.")

        self._record_thread = threading.Thread(
            target=self._mouse_and_window_loop,
            daemon=True,
            name="jarvis-macro-recorder",
        )
        self._record_thread.start()
        log.ok(f"🔴 Watch & Learn recording started for: '{self._current_name}'")

    def stop_recording(self, save_to_memory: bool = True) -> Macro:
        """Stop recording, optimize the event stream, and synthesize a Macro."""
        if not self._is_recording:
            log.warn("Macro recorder is not active.")
            return Macro(name=self._current_name, description=self._current_desc)

        self._is_recording = False
        self._stop_event.set()

        # Unhook keyboard
        if self._keyboard_unhook:
            try:
                import keyboard
                keyboard.unhook(self._keyboard_unhook)
            except Exception:
                pass
            self._keyboard_unhook = None

        if self._record_thread:
            self._record_thread.join(timeout=1.0)
            self._record_thread = None

        with self._events_lock:
            raw_copy = list(self._raw_events)

        log.info(f"Captured {len(raw_copy)} raw desktop events. Optimizing into clean macro steps...")
        macro = self._synthesize_macro(self._current_name, self._current_desc, raw_copy)

        if save_to_memory:
            path = self.mgr.save_macro(macro, sync_memory=True)
            log.ok(f"✓ Macro '{macro.name}' saved ({len(macro.steps)} steps) -> {path.name}")
        else:
            log.ok(f"✓ Macro '{macro.name}' synthesized ({len(macro.steps)} steps).")

        return macro

    def _add_event(self, kind: str, data: Dict[str, Any]) -> None:
        with self._events_lock:
            self._raw_events.append(RawEvent(kind=kind, data=data, timestamp=time.time()))

    def _on_key_event(self, event) -> None:
        if not self._is_recording:
            return
        if event.event_type == "down":
            self._add_event("key_down", {"name": event.name})
        elif event.event_type == "up":
            self._add_event("key_up", {"name": event.name})

    def _mouse_and_window_loop(self) -> None:
        """Polls mouse clicks and active window changes on Windows."""
        try:
            import win32api
            import win32con
            import win32gui
        except Exception as exc:
            log.warn(f"Win32 mouse polling unavailable: {exc}")
            return

        last_lbutton = False
        last_rbutton = False
        last_window = self._get_active_window_title()

        while not self._stop_event.is_set():
            try:
                # 1. Track Active Window Focus
                curr_window = self._get_active_window_title()
                if curr_window and curr_window != last_window:
                    self._add_event("focus_window", {"title": curr_window})
                    last_window = curr_window

                # 2. Track Mouse Left Click
                lbutton_state = (win32api.GetAsyncKeyState(win32con.VK_LBUTTON) & 0x8000) != 0
                if lbutton_state and not last_lbutton:
                    # Mouse pressed down
                    x, y = win32api.GetCursorPos()
                    el_name, el_type = self._inspect_ui_element(x, y)
                    self._add_event("click", {
                        "button": "left",
                        "x": x,
                        "y": y,
                        "element_name": el_name,
                        "control_type": el_type,
                        "window": curr_window,
                    })
                last_lbutton = lbutton_state

                # 3. Track Mouse Right Click
                rbutton_state = (win32api.GetAsyncKeyState(win32con.VK_RBUTTON) & 0x8000) != 0
                if rbutton_state and not last_rbutton:
                    x, y = win32api.GetCursorPos()
                    el_name, el_type = self._inspect_ui_element(x, y)
                    self._add_event("click", {
                        "button": "right",
                        "x": x,
                        "y": y,
                        "element_name": el_name,
                        "control_type": el_type,
                        "window": curr_window,
                    })
                last_rbutton = rbutton_state

            except Exception:
                pass

            time.sleep(0.02)  # 50Hz polling loop

    def _get_active_window_title(self) -> str:
        try:
            import win32gui
            hwnd = win32gui.GetForegroundWindow()
            if hwnd:
                return win32gui.GetWindowText(hwnd).strip()
        except Exception:
            pass
        return ""

    def _inspect_ui_element(self, x: int, y: int) -> Tuple[str, str]:
        """Inspect UI Automation control under mouse cursor."""
        try:
            import uiautomation as auto
            ctrl = auto.ControlFromPoint(x, y)
            if ctrl:
                name = (ctrl.Name or "").strip()
                ctype = (ctrl.ControlTypeName or "").replace("Control", "").strip()
                return name, ctype
        except Exception:
            pass
        return "", ""

    # ------------------------------------------------------------------ #
    # Event Optimization & Macro Synthesis
    # ------------------------------------------------------------------ #

    def _synthesize_macro(self, name: str, desc: str, raw_events: List[RawEvent]) -> Macro:
        """Convert raw event stream into clean, high-level Macro steps."""
        steps: List[MacroStep] = []
        target_apps: List[str] = []
        app_names_seen = set()

        i = 0
        n = len(raw_events)
        active_modifiers = set()

        while i < n:
            ev = raw_events[i]
            kind = ev.kind
            data = ev.data

            if kind == "focus_window":
                title = data.get("title", "")
                if title:
                    # Detect target app name
                    app_match = title.split(" - ")[-1] if " - " in title else title
                    if app_match and app_match not in app_names_seen:
                        target_apps.append(app_match)
                        app_names_seen.add(app_match)

                    # Only emit focus_window if not already the last step
                    if not steps or steps[-1].action != "focus_window" or steps[-1].args.get("title") != title:
                        steps.append(MacroStep(
                            action="focus_window",
                            args={"title": title},
                            description=f"Focus window '{title}'",
                            delay=0.2,
                        ))
                i += 1

            elif kind == "click":
                x, y = data.get("x", 0), data.get("y", 0)
                btn = data.get("button", "left")
                el_name = data.get("element_name", "")
                ctype = data.get("control_type", "")

                # Check for double click (consecutive clicks at same spot within 0.35s)
                action_name = "double_click" if (
                    steps and steps[-1].action == "click"
                    and abs(steps[-1].args.get("x", 0) - x) < 5
                    and abs(steps[-1].args.get("y", 0) - y) < 5
                ) else ("right_click" if btn == "right" else "click")

                if action_name == "double_click":
                    steps[-1].action = "double_click"
                    steps[-1].description = f"Double-click at ({x}, {y})"
                else:
                    label = f"'{el_name}' {ctype}" if el_name else f"at ({x}, {y})"
                    steps.append(MacroStep(
                        action=action_name,
                        args={"x": x, "y": y, "element_name": el_name, "control_type": ctype},
                        description=f"Click {label}",
                        delay=0.25,
                    ))
                i += 1

            elif kind == "key_down":
                key_name = data.get("name", "").lower()

                # Track modifiers
                if key_name in {"ctrl", "control", "left ctrl", "right ctrl"}:
                    active_modifiers.add("ctrl")
                    i += 1
                    continue
                elif key_name in {"alt", "left alt", "right alt"}:
                    active_modifiers.add("alt")
                    i += 1
                    continue
                elif key_name in {"shift", "left shift", "right shift"}:
                    active_modifiers.add("shift")
                    i += 1
                    continue
                elif key_name in {"windows", "win", "left windows"}:
                    active_modifiers.add("win")
                    i += 1
                    continue

                # 1. Hotkey combination
                if active_modifiers:
                    combo_parts = sorted(list(active_modifiers)) + [key_name]
                    combo_str = "+".join(combo_parts)
                    steps.append(MacroStep(
                        action="press",
                        args={"keys": combo_str},
                        description=f"Press shortcut '{combo_str}'",
                        delay=0.15,
                    ))
                    i += 1
                    continue

                # 2. Printable characters -> Coalesce into type("...")
                if len(key_name) == 1 and key_name in string.printable and key_name not in {"\t", "\n", "\r"}:
                    typed_chars = [key_name]
                    j = i + 1
                    while j < n:
                        next_ev = raw_events[j]
                        if next_ev.kind == "key_down":
                            k_name = next_ev.data.get("name", "")
                            if len(k_name) == 1 and k_name in string.printable and k_name not in {"\t", "\n", "\r"}:
                                typed_chars.append(k_name)
                                j += 1
                            elif k_name == "space":
                                typed_chars.append(" ")
                                j += 1
                            else:
                                break
                        elif next_ev.kind in {"key_up"}:
                            j += 1
                        else:
                            break

                    typed_str = "".join(typed_chars)
                    steps.append(MacroStep(
                        action="type",
                        args={"text": typed_str},
                        description=f'Type "{typed_str}"',
                        delay=0.2,
                    ))
                    i = j

                # 3. Special keys (enter, tab, backspace, esc, delete, arrows, etc.)
                else:
                    norm_key = key_name
                    if norm_key == "space":
                        norm_key = "space"
                    steps.append(MacroStep(
                        action="press",
                        args={"keys": norm_key},
                        description=f"Press key '{norm_key}'",
                        delay=0.15,
                    ))
                    i += 1

            elif kind == "key_up":
                key_name = data.get("name", "").lower()
                if key_name in {"ctrl", "control", "left ctrl", "right ctrl"}:
                    active_modifiers.discard("ctrl")
                elif key_name in {"alt", "left alt", "right alt"}:
                    active_modifiers.discard("alt")
                elif key_name in {"shift", "left shift", "right shift"}:
                    active_modifiers.discard("shift")
                elif key_name in {"windows", "win", "left windows"}:
                    active_modifiers.discard("win")
                i += 1

            else:
                i += 1

        return Macro(
            name=name,
            description=desc or f"Automated workflow for {', '.join(target_apps) if target_apps else 'desktop'}",
            steps=steps,
            target_apps=target_apps,
        )


_GLOBAL_RECORDER: Optional[MacroRecorder] = None


def get_macro_recorder(macro_manager=None) -> MacroRecorder:
    global _GLOBAL_RECORDER
    if _GLOBAL_RECORDER is None:
        _GLOBAL_RECORDER = MacroRecorder(macro_manager=macro_manager)
    return _GLOBAL_RECORDER
