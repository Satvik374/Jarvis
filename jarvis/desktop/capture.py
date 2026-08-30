"""Shadow Desktop Frame Capture Engine.

Captures visual frames and screenshots of windows running inside the isolated
Shadow Desktop session using Win32 PrintWindow (PW_RENDERFULLCONTENT) and GDI DCs
directly without rendering over the user's primary monitor.
"""

from __future__ import annotations

import sys
import ctypes
from typing import Optional, Tuple
from PIL import Image

from ..utils import logging as log

from .manager import get_shadow_manager, ShadowWindow

PW_RENDERFULLCONTENT = 0x00000002
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
BI_RGB = 0


class ShadowCaptureEngine:
    """Captures background screenshots from the Shadow Desktop."""

    def __init__(self, default_resolution: Tuple[int, int] = (1920, 1080)):
        self.default_res = default_resolution
        self._is_windows = sys.platform == "win32"
        self._user32 = ctypes.windll.user32 if self._is_windows else None
        self._gdi32 = ctypes.windll.gdi32 if self._is_windows else None

    def capture_window(self, hwnd: int) -> Optional[Image.Image]:
        """Capture a specific window using PrintWindow (PW_RENDERFULLCONTENT)."""
        if not self._is_windows:
            return None

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", ctypes.c_uint32),
                ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32),
                ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16),
                ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32),
                ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32),
                ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32),
            ]

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [
                ("bmiHeader", BITMAPINFOHEADER),
                ("bmiColors", ctypes.c_ulong * 3),
            ]

        try:
            rect = RECT()
            self._user32.GetWindowRect(hwnd, ctypes.byref(rect))
            w = rect.right - rect.left
            h = rect.bottom - rect.top

            if w <= 0 or h <= 0:
                return None

            hdc_win = self._user32.GetWindowDC(hwnd)
            hdc_mem = self._gdi32.CreateCompatibleDC(hdc_win)
            hbmp = self._gdi32.CreateCompatibleBitmap(hdc_win, w, h)
            self._gdi32.SelectObject(hdc_mem, hbmp)

            # Try PW_RENDERFULLCONTENT first
            printed = self._user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT)
            if not printed:
                # Fallback to standard PrintWindow
                self._user32.PrintWindow(hwnd, hdc_mem, 0)

            bmi = BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = w
            bmi.bmiHeader.biHeight = -h  # top-down DIB
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = BI_RGB

            buffer_len = w * h * 4
            buffer = ctypes.create_string_buffer(buffer_len)

            self._gdi32.GetDIBits(
                hdc_mem,
                hbmp,
                0,
                h,
                buffer,
                ctypes.byref(bmi),
                DIB_RGB_COLORS
            )

            # Cleanup Win32 GDI objects
            self._gdi32.DeleteObject(hbmp)
            self._gdi32.DeleteDC(hdc_mem)
            self._user32.ReleaseDC(hwnd, hdc_win)

            # Convert BGRA to RGB PIL Image
            raw_bytes = buffer.raw
            img = Image.frombuffer("RGBA", (w, h), raw_bytes, "raw", "BGRA", 0, 1)
            return img.convert("RGB")
        except Exception as exc:
            log.warn(f"Failed to capture shadow window {hwnd}: {exc}")
            return None

    def capture_shadow_desktop(self) -> Image.Image:
        """Capture the current active state of the Shadow Desktop as a composite PIL image."""
        mgr = get_shadow_manager()
        windows = mgr.list_windows()

        if not windows:
            # Render synthetic background canvas
            bg = Image.new("RGB", self.default_res, color=(16, 20, 32))
            return bg

        # If we have active windows, capture the foreground/topmost one
        top_win = windows[0]
        img = self.capture_window(top_win.hwnd)
        if img:
            return img

        return Image.new("RGB", self.default_res, color=(16, 20, 32))


_CAPTURE_ENGINE = ShadowCaptureEngine()


def get_shadow_capture() -> ShadowCaptureEngine:
    return _CAPTURE_ENGINE
