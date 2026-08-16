"""Launch, focus, and enumerate applications and windows (Windows-first)."""

from __future__ import annotations

import os
import subprocess
import time


# Common friendly names -> launch command on Windows.
_KNOWN = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "explorer": "explorer.exe",
    "files": "explorer.exe",
    "file explorer": "explorer.exe",
    "paint": "mspaint.exe",
    "cmd": "cmd.exe",
    "terminal": "wt.exe",
    "powershell": "powershell.exe",
    "task manager": "taskmgr.exe",
    "settings": "ms-settings:",
    "chrome": "chrome",
    "edge": "msedge",
    "firefox": "firefox",
    "word": "winword",
    "excel": "excel",
    "vscode": "code",
    "vs code": "code",
    "code": "code",
    "spotify": "spotify",
}


def open_app(name: str) -> str:
    """Launch (or bring up) an application by friendly name or executable."""
    key = name.strip().lower()
    target = _KNOWN.get(key, name)

    # Try to focus it first if a matching window already exists.
    if focus_window(name).startswith("focused"):
        return f"focused existing '{name}'"

    if target.startswith("ms-settings:") or target.startswith("http"):
        os.startfile(target)  # type: ignore[attr-defined]
        return f"opened '{target}'"

    try:
        if os.name == "nt":
            # ``start`` resolves App Paths / PATH like the Run dialog does.
            subprocess.Popen(["cmd", "/c", "start", "", target], shell=False)
        else:
            subprocess.Popen([target])
        time.sleep(1.0)
        return f"launched '{name}'"
    except Exception as exc:
        # Last resort: hand it to the shell / Start.
        try:
            os.startfile(target)  # type: ignore[attr-defined]
            return f"opened '{name}'"
        except Exception:
            return f"could not launch '{name}': {exc}"


def focus_window(title: str) -> str:
    """Activate the first window whose title contains ``title``."""
    try:
        import pygetwindow as gw  # type: ignore
    except Exception:
        return "pygetwindow unavailable"

    matches = [w for w in gw.getAllWindows()
               if w.title and title.lower() in w.title.lower()]
    if not matches:
        return f"no window matching '{title}'"
    w = matches[0]
    try:
        if w.isMinimized:
            w.restore()
        w.activate()
        return f"focused '{w.title}'"
    except Exception as exc:
        return f"found '{w.title}' but could not focus: {exc}"


def _own_console_ids() -> tuple[int, str]:
    """(hwnd, title) of the console window Jarvis runs in; (0, '') if none."""
    try:
        import ctypes

        k = ctypes.windll.kernel32
        hwnd = int(k.GetConsoleWindow() or 0)
        buf = ctypes.create_unicode_buffer(512)
        title = buf.value if k.GetConsoleTitleW(buf, 512) else ""
        return hwnd, title.strip()
    except Exception:
        return 0, ""


_OWN_BROWSER_TITLE = "JARVIS // NEURAL INTERFACE"


def _is_own_console(w, own: tuple | None = None) -> bool:
    """True for Jarvis's terminal or its optional browser interface.

    Matches by console HWND, title equality as a fallback for Windows Terminal
    (where the real console is a hidden ConPTY window), and the browser page's
    deliberately unique title.  Protecting both surfaces keeps a desktop task
    from accidentally closing the interface that is supervising it.
    """
    hwnd, title = own if own is not None else _own_console_ids()
    if hwnd and getattr(w, "_hWnd", None) == hwnd:
        return True
    wt = (getattr(w, "title", "") or "").strip()
    return (bool(title) and wt == title) or _OWN_BROWSER_TITLE in wt


def active_is_own_console() -> bool:
    try:
        import pygetwindow as gw  # type: ignore

        w = gw.getActiveWindow()
        return w is not None and _is_own_console(w)
    except Exception:
        return False


def close_window(title: str) -> str:
    """Gracefully close the first window whose title contains ``title``.
    Refuses to close the terminal Jarvis itself is running in."""
    title = (title or "").strip()
    if not title:
        return "close_window needs a title"
    try:
        import pygetwindow as gw  # type: ignore
    except Exception:
        return "pygetwindow unavailable"

    matches = [w for w in gw.getAllWindows()
               if w.title and title.lower() in w.title.lower()]
    if not matches:
        return f"no window matching '{title}'"
    safe = [w for w in matches if not _is_own_console(w)]
    if not safe:
        return ("refused: that window is Jarvis's own interface - closing it "
                "would kill Jarvis")
    w = safe[0]
    try:
        w.close()
        return f"closed '{w.title}'"
    except Exception as exc:
        return f"found '{w.title}' but could not close: {exc}"


def list_windows() -> list[str]:
    try:
        import pygetwindow as gw  # type: ignore

        return [w.title for w in gw.getAllWindows() if w.title.strip()]
    except Exception:
        return []


def snap_window(direction: str, title: str | None = None) -> str:
    """Position the active or named window (left, right, top, bottom, maximize, minimize, restore, center)."""
    dir_clean = (direction or "").strip().lower()
    try:
        import pygetwindow as gw  # type: ignore
        from ..perception.screen import screen_size
    except Exception as exc:
        return f"window manager unavailable: {exc}"

    if title:
        matches = [w for w in gw.getAllWindows() if w.title and title.lower() in w.title.lower()]
        if not matches:
            return f"no window matching '{title}'"
        w = matches[0]
    else:
        w = gw.getActiveWindow()
        if not w:
            return "no active window found to snap"

    sw, sh = screen_size()

    try:
        if dir_clean in ("maximize", "max"):
            w.maximize()
            return f"maximized '{w.title}'"
        elif dir_clean in ("minimize", "min"):
            w.minimize()
            return f"minimized '{w.title}'"
        elif dir_clean == "restore":
            w.restore()
            return f"restored '{w.title}'"
        elif dir_clean == "left":
            if w.isMinimized or w.isMaximized:
                w.restore()
            w.moveTo(0, 0)
            w.resizeTo(sw // 2, sh)
            return f"snapped '{w.title}' to left half"
        elif dir_clean == "right":
            if w.isMinimized or w.isMaximized:
                w.restore()
            w.moveTo(sw // 2, 0)
            w.resizeTo(sw // 2, sh)
            return f"snapped '{w.title}' to right half"
        elif dir_clean == "top":
            if w.isMinimized or w.isMaximized:
                w.restore()
            w.moveTo(0, 0)
            w.resizeTo(sw, sh // 2)
            return f"snapped '{w.title}' to top half"
        elif dir_clean == "bottom":
            if w.isMinimized or w.isMaximized:
                w.restore()
            w.moveTo(0, sh // 2)
            w.resizeTo(sw, sh // 2)
            return f"snapped '{w.title}' to bottom half"
        elif dir_clean == "center":
            if w.isMinimized or w.isMaximized:
                w.restore()
            cw, ch = int(sw * 0.7), int(sh * 0.7)
            w.moveTo((sw - cw) // 2, (sh - ch) // 2)
            w.resizeTo(cw, ch)
            return f"centered '{w.title}'"
        else:
            return f"unknown direction '{direction}'. Supported: left, right, top, bottom, maximize, minimize, restore, center"
    except Exception as exc:
        return f"could not snap '{w.title}': {exc}"


def tile_windows(layout: str = "side_by_side") -> str:
    """Tile open visible windows into a clean desktop layout (side_by_side, grid, minimize_all)."""
    layout_clean = (layout or "side_by_side").strip().lower()
    try:
        import pygetwindow as gw  # type: ignore
        from ..perception.screen import screen_size
    except Exception as exc:
        return f"window manager unavailable: {exc}"

    sw, sh = screen_size()
    windows = [w for w in gw.getAllWindows() if w.title.strip() and not _is_own_console(w) and not w.isMinimized]

    if not windows:
        return "no visible windows to tile"

    if layout_clean == "minimize_all":
        count = 0
        for w in windows:
            try:
                w.minimize()
                count += 1
            except Exception:
                pass
        return f"minimized {count} open windows"

    if layout_clean in ("side_by_side", "horizontal"):
        n = min(len(windows), 4)
        width = sw // n
        for i, w in enumerate(windows[:n]):
            try:
                if w.isMaximized:
                    w.restore()
                w.moveTo(i * width, 0)
                w.resizeTo(width, sh)
            except Exception:
                pass
        return f"tiled {n} windows side-by-side"

    if layout_clean in ("grid", "2x2"):
        n = min(len(windows), 4)
        if n == 1:
            return snap_window("maximize", windows[0].title)
        w_half, h_half = sw // 2, sh // 2
        coords = [(0, 0), (w_half, 0), (0, h_half), (w_half, h_half)]
        for i, w in enumerate(windows[:n]):
            try:
                if w.isMaximized:
                    w.restore()
                x, y = coords[i]
                w.moveTo(x, y)
                w.resizeTo(w_half, h_half)
            except Exception:
                pass
        return f"tiled {n} windows in 2x2 grid"

    return f"unknown layout '{layout}'. Supported: side_by_side, grid, minimize_all"

