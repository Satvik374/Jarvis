"""Virtual Input Dispatcher for Shadow Desktop.

Dispatches mouse clicks, keystrokes, typing, and scroll events directly to target
window HWNDs via Win32 messages (PostMessage / SendMessage), completely bypassing
the physical system cursor so the user's mouse and keyboard remain undisturbed.
"""

from __future__ import annotations

import sys
import time
import ctypes
from typing import Optional, Tuple, Dict, Any

from ..utils import logging as log

from .manager import get_shadow_manager

# Win32 Window Messages
WM_NULL = 0x0000
WM_CREATE = 0x0001
WM_DESTROY = 0x0002
WM_SETFOCUS = 0x0007
WM_KILLFOCUS = 0x0008
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_UNICHAR = 0x0109
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_RBUTTONDBLCLK = 0x0206
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MOUSEWHEEL = 0x020A
MK_LBUTTON = 0x0001
MK_RBUTTON = 0x0002

# Virtual Key Codes
VK_MAP: Dict[str, int] = {
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
    "backspace": 0x08,
    "bksp": 0x08,
    "escape": 0x1B,
    "esc": 0x1B,
    "space": 0x20,
    "pageup": 0x21,
    "pagedown": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "insert": 0x2D,
    "delete": 0x2E,
    "del": 0x2E,
    "f1": 0x70,
    "f2": 0x71,
    "f3": 0x72,
    "f4": 0x73,
    "f5": 0x74,
    "f6": 0x75,
    "f7": 0x76,
    "f8": 0x77,
    "f9": 0x78,
    "f10": 0x79,
    "f11": 0x7A,
    "f12": 0x7B,
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "shift": 0x10,
    "win": 0x5B,
}


def _makelparam(x: int, y: int) -> int:
    return (int(y) << 16) | (int(x) & 0xFFFF)


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class VirtualInputDispatcher:
    """Dispatches headless virtual input events directly to target windows."""

    def __init__(self):
        self._is_windows = sys.platform == "win32"
        self._user32 = ctypes.windll.user32 if self._is_windows else None

    def _resolve_hwnd(self, hwnd: Optional[int] = None) -> int:
        if hwnd:
            return hwnd
        mgr = get_shadow_manager()
        windows = mgr.list_windows()
        if windows:
            return windows[0].hwnd
        return 0

    def click(self, x: int, y: int, hwnd: Optional[int] = None,
              button: str = "left", clicks: int = 1) -> bool:
        """Inject a synthetic mouse click at (x, y) on the target window."""
        if not self._is_windows:
            return False

        target_hwnd = self._resolve_hwnd(hwnd)
        if not target_hwnd:
            log.warn("VirtualInput click skipped: No target window available in shadow workspace.")
            return False

        # Convert screen coordinates to client coordinates
        try:
            pt = POINT(int(x), int(y))
            self._user32.ScreenToClient(target_hwnd, ctypes.byref(pt))
            client_x, client_y = pt.x, pt.y
        except Exception:
            client_x, client_y = int(x), int(y)

        lparam = _makelparam(client_x, client_y)

        btn = button.lower()
        down_msg = WM_RBUTTONDOWN if btn == "right" else WM_LBUTTONDOWN
        up_msg = WM_RBUTTONUP if btn == "right" else WM_LBUTTONUP
        flags = MK_RBUTTON if btn == "right" else MK_LBUTTON

        try:
            # Send focus & mouse move
            self._user32.PostMessageW(target_hwnd, WM_SETFOCUS, 0, 0)
            self._user32.PostMessageW(target_hwnd, WM_MOUSEMOVE, 0, lparam)

            for _ in range(clicks):
                self._user32.PostMessageW(target_hwnd, down_msg, flags, lparam)
                time.sleep(0.01)
                self._user32.PostMessageW(target_hwnd, up_msg, 0, lparam)
                if clicks > 1:
                    time.sleep(0.05)

            return True
        except Exception as exc:
            log.warn(f"VirtualInput click failed: {exc}")
            return False

    def type_text(self, text: str, hwnd: Optional[int] = None) -> bool:
        """Inject characters directly into the target window message queue."""
        if not self._is_windows:
            return False

        target_hwnd = self._resolve_hwnd(hwnd)
        if not target_hwnd:
            log.warn("VirtualInput type_text skipped: No target window available in shadow workspace.")
            return False

        try:
            self._user32.PostMessageW(target_hwnd, WM_SETFOCUS, 0, 0)
            for ch in text:
                char_code = ord(ch)
                self._user32.PostMessageW(target_hwnd, WM_CHAR, char_code, 1)
                time.sleep(0.005)
            return True
        except Exception as exc:
            log.warn(f"VirtualInput type_text failed: {exc}")
            return False

    def press_key(self, key: str, hwnd: Optional[int] = None) -> bool:
        """Inject a single key press (e.g. 'enter', 'tab', 'backspace')."""
        if not self._is_windows:
            return False

        target_hwnd = self._resolve_hwnd(hwnd)
        if not target_hwnd:
            log.warn("VirtualInput press_key skipped: No target window available in shadow workspace.")
            return False

        k = key.lower().strip()
        vk = VK_MAP.get(k)
        if vk is None:
            if len(k) == 1:
                vk = ord(k.upper())
            else:
                return False

        try:
            self._user32.PostMessageW(target_hwnd, WM_SETFOCUS, 0, 0)
            self._user32.PostMessageW(target_hwnd, WM_KEYDOWN, vk, 1)
            time.sleep(0.01)
            self._user32.PostMessageW(target_hwnd, WM_KEYUP, vk, 0xC0000001)
            return True
        except Exception as exc:
            log.warn(f"VirtualInput press_key failed: {exc}")
            return False

    def scroll(self, clicks: int, x: int = 0, y: int = 0, hwnd: Optional[int] = None) -> bool:
        """Inject vertical scroll wheel delta."""
        if not self._is_windows:
            return False

        target_hwnd = self._resolve_hwnd(hwnd)
        if not target_hwnd:
            return False

        lparam = _makelparam(x, y)
        wparam = (int(clicks) * 120) << 16
        try:
            self._user32.PostMessageW(target_hwnd, WM_MOUSEWHEEL, wparam, lparam)
            return True
        except Exception as exc:
            log.warn(f"VirtualInput scroll failed: {exc}")
            return False



_VIRTUAL_INPUT = VirtualInputDispatcher()


def get_virtual_input() -> VirtualInputDispatcher:
    return _VIRTUAL_INPUT
