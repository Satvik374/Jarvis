"""Shadow Desktop Manager.

Provides isolated Win32 Desktop session lifecycle management, process spawning
into the shadow workspace, window enumeration, and window promotion (handoff).
"""

from __future__ import annotations

import os
import sys
import time
import ctypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..utils import logging as log


# Win32 Constants
DESKTOP_CREATEWINDOW = 0x0002
DESKTOP_ENUMERATE = 0x0040
DESKTOP_WRITEOBJECTS = 0x0080
DESKTOP_SWITCHDESKTOP = 0x0100
DESKTOP_CREATEMENU = 0x0004
DESKTOP_HOOKCONTROL = 0x0020
DESKTOP_READOBJECTS = 0x0001
DESKTOP_JOURNALRECORD = 0x0010
DESKTOP_JOURNALPLAYBACK = 0x0008
GENERIC_ALL = 0x10000000
DESKTOP_ALL = (
    DESKTOP_READOBJECTS | DESKTOP_CREATEWINDOW | DESKTOP_CREATEMENU |
    DESKTOP_HOOKCONTROL | DESKTOP_JOURNALRECORD | DESKTOP_JOURNALPLAYBACK |
    DESKTOP_ENUMERATE | DESKTOP_WRITEOBJECTS | DESKTOP_SWITCHDESKTOP
)

SW_SHOW = 5
SW_RESTORE = 9


@dataclass
class ShadowWindow:
    hwnd: int
    title: str
    class_name: str
    rect: Tuple[int, int, int, int]  # (left, top, right, bottom)
    is_visible: bool

    @property
    def width(self) -> int:
        return self.rect[2] - self.rect[0]

    @property
    def height(self) -> int:
        return self.rect[3] - self.rect[1]

    @property
    def center(self) -> Tuple[int, int]:
        return (self.rect[0] + self.rect[2]) // 2, (self.rect[1] + self.rect[3]) // 2


class ShadowDesktopManager:
    """Manages the lifecycle of an isolated Shadow Desktop session on Windows."""

    _instance: Optional[ShadowDesktopManager] = None

    def __init__(self, desktop_name: str = "JarvisShadowDesktop", enabled: bool = False):
        self.desktop_name = desktop_name
        self.enabled = enabled
        self._h_desktop: Optional[int] = None
        self._h_default_desktop: Optional[int] = None
        self._tracked_pids: List[int] = []
        self._is_windows = sys.platform == "win32"
        self._user32 = ctypes.windll.user32 if self._is_windows else None
        self._kernel32 = ctypes.windll.kernel32 if self._is_windows else None

    @classmethod
    def get_instance(cls, desktop_name: str = "JarvisShadowDesktop", enabled: bool = False) -> ShadowDesktopManager:
        if cls._instance is None:
            cls._instance = cls(desktop_name=desktop_name, enabled=enabled)
        return cls._instance

    # ------------------------------------------------------------------ #
    # State & Toggles
    # ------------------------------------------------------------------ #

    def is_enabled(self) -> bool:
        return self.enabled

    def enable(self) -> bool:
        self.enabled = True
        if self._is_windows:
            return self.ensure_desktop()
        return True

    def disable(self) -> None:
        self.enabled = False

    def toggle(self) -> bool:
        if self.enabled:
            self.disable()
        else:
            self.enable()
        return self.enabled

    # ------------------------------------------------------------------ #
    # Desktop Lifecycle
    # ------------------------------------------------------------------ #

    def ensure_desktop(self) -> bool:
        """Create or open the shadow desktop handle."""
        if not self._is_windows:
            return True

        if self._h_desktop:
            return True

        try:
            # Save the current thread desktop (Default)
            self._h_default_desktop = self._user32.GetThreadDesktop(self._kernel32.GetCurrentThreadId())

            # Try to create or open the desktop
            h_desk = self._user32.CreateDesktopW(
                self.desktop_name,
                None,
                None,
                0,
                GENERIC_ALL,
                None
            )

            if not h_desk:
                # Open existing if already created
                h_desk = self._user32.OpenDesktopW(
                    self.desktop_name,
                    0,
                    False,
                    GENERIC_ALL
                )

            if h_desk:
                self._h_desktop = h_desk
                log.info(f"🌌 Shadow Desktop initialized: '{self.desktop_name}' (handle={h_desk})")
                return True
            else:
                err = ctypes.GetLastError()
                log.warn(f"Failed to create/open shadow desktop: win32 error code {err}")
                return False
        except Exception as exc:
            log.warn(f"Exception initializing shadow desktop: {exc}")
            return False

    def close(self) -> None:
        """Close shadow desktop handle and cleanup."""
        if not self._is_windows or not self._h_desktop:
            return

        try:
            self._user32.CloseDesktop(self._h_desktop)
            self._h_desktop = None
            log.info("🌌 Shadow Desktop closed.")
        except Exception as exc:
            log.warn(f"Error closing shadow desktop: {exc}")

    # ------------------------------------------------------------------ #
    # Process Spawning in Shadow Desktop
    # ------------------------------------------------------------------ #

    def spawn_process(self, command: str, cwd: Optional[str] = None) -> Optional[int]:
        """Spawn a process attached directly to the shadow desktop session."""
        if not self._is_windows or not self.enabled:
            import subprocess
            proc = subprocess.Popen(command, shell=True, cwd=cwd)
            self._tracked_pids.append(proc.pid)
            return proc.pid

        self.ensure_desktop()

        try:
            # Use Win32 CreateProcess with STARTUPINFO.lpDesktop
            class STARTUPINFOW(ctypes.Structure):
                _fields_ = [
                    ("cb", ctypes.c_uint32),
                    ("lpReserved", ctypes.c_wchar_p),
                    ("lpDesktop", ctypes.c_wchar_p),
                    ("lpTitle", ctypes.c_wchar_p),
                    ("dwX", ctypes.c_uint32),
                    ("dwY", ctypes.c_uint32),
                    ("dwXSize", ctypes.c_uint32),
                    ("dwYSize", ctypes.c_uint32),
                    ("dwXCountChars", ctypes.c_uint32),
                    ("dwYCountChars", ctypes.c_uint32),
                    ("dwFillAttribute", ctypes.c_uint32),
                    ("dwFlags", ctypes.c_uint32),
                    ("wShowWindow", ctypes.c_uint16),
                    ("cbReserved2", ctypes.c_uint16),
                    ("lpReserved2", ctypes.c_void_p),
                    ("hStdInput", ctypes.c_void_p),
                    ("hStdOutput", ctypes.c_void_p),
                    ("hStdError", ctypes.c_void_p),
                ]

            class PROCESS_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("hProcess", ctypes.c_void_p),
                    ("hThread", ctypes.c_void_p),
                    ("dwProcessId", ctypes.c_uint32),
                    ("dwThreadId", ctypes.c_uint32),
                ]

            si = STARTUPINFOW()
            si.cb = ctypes.sizeof(STARTUPINFOW)
            si.lpDesktop = self.desktop_name
            si.dwFlags = 0x00000001  # STARTF_USESHOWWINDOW
            si.wShowWindow = SW_SHOW

            pi = PROCESS_INFORMATION()

            # Format full command line safely with start ""
            if command.startswith(("http://", "https://", "spotify:", "ms-settings:")):
                cmd_line = f'cmd.exe /c start "" "{command}"'
            elif not command.endswith(".exe"):
                cmd_line = f'cmd.exe /c start "" {command}'
            else:
                cmd_line = command


            success = self._kernel32.CreateProcessW(
                None,
                cmd_line,
                None,
                None,
                False,
                0,
                None,
                cwd,
                ctypes.byref(si),
                ctypes.byref(pi)
            )

            if success:
                pid = pi.dwProcessId
                self._tracked_pids.append(pid)
                self._kernel32.CloseHandle(pi.hProcess)
                self._kernel32.CloseHandle(pi.hThread)
                log.info(f"🌌 Spawned shadow process: '{command}' (PID {pid})")
                return pid
            else:
                err = ctypes.GetLastError()
                log.warn(f"Failed to spawn shadow process: win32 error {err}. Falling back to standard spawn.")
                import subprocess
                proc = subprocess.Popen(command, shell=True, cwd=cwd)
                self._tracked_pids.append(proc.pid)
                return proc.pid
        except Exception as exc:
            log.warn(f"Exception spawning shadow process: {exc}")
            import subprocess
            proc = subprocess.Popen(command, shell=True, cwd=cwd)
            self._tracked_pids.append(proc.pid)
            return proc.pid

    def spawn_url(self, url: str) -> Optional[int]:
        """Spawn an isolated browser instance inside the shadow desktop session."""
        browser_exe = self.find_browser_exe()
        if browser_exe:
            profile_dir = Path.home() / ".jarvis" / "shadow_browser_profile"
            profile_dir.mkdir(parents=True, exist_ok=True)
            cmd = f'"{browser_exe}" --user-data-dir="{profile_dir}" --no-first-run --new-window "{url}"'
            return self.spawn_process(cmd)
        return self.spawn_process(url)

    @staticmethod
    def find_browser_exe() -> Optional[str]:
        """Locate standalone Chrome or Edge browser executable on Windows."""
        candidates = [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
        ]
        for c in candidates:
            if os.path.isfile(c):
                return c
        return None


    # ------------------------------------------------------------------ #
    # Window Enumeration & Discovery
    # ------------------------------------------------------------------ #

    def list_windows(self) -> List[ShadowWindow]:
        """Enumerate top-level windows residing on the shadow desktop."""
        if not self._is_windows or not self._h_desktop:
            return []

        windows: List[ShadowWindow] = []
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        def enum_handler(hwnd, lparam):
            if not self._user32.IsWindowVisible(hwnd):
                return True

            title_buf = ctypes.create_unicode_buffer(512)
            self._user32.GetWindowTextW(hwnd, title_buf, 512)
            title = title_buf.value.strip()

            class_buf = ctypes.create_unicode_buffer(256)
            self._user32.GetClassNameW(hwnd, class_buf, 256)
            class_name = class_buf.value.strip()

            rect = RECT()
            self._user32.GetWindowRect(hwnd, ctypes.byref(rect))

            if rect.right > rect.left and rect.bottom > rect.top and (title or class_name not in ("Progman", "Shell_TrayWnd")):
                windows.append(ShadowWindow(
                    hwnd=hwnd,
                    title=title,
                    class_name=class_name,
                    rect=(rect.left, rect.top, rect.right, rect.bottom),
                    is_visible=True
                ))
            return True

        cb = WNDENUMPROC(enum_handler)
        try:
            self._user32.EnumDesktopWindows(self._h_desktop, cb, 0)
            # Sort windows so titled and larger windows come first (primary interactive apps)
            windows.sort(key=lambda w: (bool(w.title.strip()), (w.rect[2]-w.rect[0]) * (w.rect[3]-w.rect[1])), reverse=True)
        except Exception as exc:
            log.warn(f"Error enumerating shadow desktop windows: {exc}")

        return windows

    def find_window(self, query: str) -> Optional[ShadowWindow]:
        """Find a shadow window by title or class substring match."""
        q = query.lower()
        windows = self.list_windows()
        for win in windows:
            if q in win.title.lower() or q in win.class_name.lower():
                return win
        return None

    # ------------------------------------------------------------------ #
    # Window Promotion (Handoff to Default Desktop)
    # ------------------------------------------------------------------ #

    def promote_window(self, query_or_hwnd: Any) -> bool:
        """Move or display a window from the shadow desktop to the user's main desktop."""
        if not self._is_windows:
            return False

        target_hwnd = query_or_hwnd if isinstance(query_or_hwnd, int) else None
        if target_hwnd is None:
            win = self.find_window(str(query_or_hwnd))
            if win:
                target_hwnd = win.hwnd

        if not target_hwnd:
            log.warn(f"No window found matching '{query_or_hwnd}' to promote.")
            return False

        try:
            # Switch desktop view or bring window to foreground
            self._user32.ShowWindow(target_hwnd, SW_RESTORE)
            self._user32.SetForegroundWindow(target_hwnd)
            log.ok(f"🌌 Promoted window {target_hwnd} to active view.")
            return True
        except Exception as exc:
            log.warn(f"Failed to promote window: {exc}")
            return False


def get_shadow_manager() -> ShadowDesktopManager:
    return ShadowDesktopManager.get_instance()


def is_shadow_enabled() -> bool:
    return get_shadow_manager().is_enabled()


def toggle_shadow() -> bool:
    return get_shadow_manager().toggle()


def set_shadow_enabled(enabled: bool) -> None:
    mgr = get_shadow_manager()
    if enabled:
        mgr.enable()
    else:
        mgr.disable()
