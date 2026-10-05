"""Launch, focus, and enumerate applications and windows (Windows-first)."""

from __future__ import annotations

import os
import re
import subprocess
import time

from . import app_index

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
    "spotify": "spotify:",
}


#: How long ``open_app`` waits for the launched app's own window to show up.
#: Bounded, because this is spent *after* the app has started: a quick app is
#: confirmed in ~60 ms and a slow one is reported as "still starting" rather
#: than slept through for a second and then claimed as settled.
_WINDOW_WAIT = 1.5
_WINDOW_POLL = 0.06

#: Names that must not be matched against the index, because their launch target
#: needs the special handling below (browser executables, the Store Spotify).
_SPECIAL = frozenset({"spotify", "chrome", "google chrome", "edge", "msedge",
                      "microsoft edge"})


def _evidence_words(*names: str) -> tuple[str, ...]:
    """Words worth matching a window title against - long enough to be a signal.

    "the", "app" and two-letter fragments appear in dozens of unrelated window
    titles, so they would report a launch as settled on the strength of the
    wrong window.
    """
    words: list[str] = []
    for text in names:
        for word in re.split(r"[^\w]+", str(text or "").lower()):
            if len(word) > 2 and word not in words:
                words.append(word)
    return tuple(words)


def _window_evidence(names: tuple[str, ...], timeout: float = _WINDOW_WAIT) -> str:
    """Title of a window matching one of ``names``, or "" if none appeared.

    This replaces the unconditional ``time.sleep(1.0)`` ``open_app`` used to do.
    The window list is the only honest proof a launch worked, so the wait is
    spent *looking* rather than sleeping: it returns the instant the window
    exists, and gives up (returning nothing) after ``timeout``.
    """
    try:
        import pygetwindow  # type: ignore  # noqa: F401
    except Exception:
        return ""          # no window list on this machine: nothing to wait for
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        try:
            titles = list_windows()
        except Exception:
            return ""
        for title in titles:
            lowered = title.lower()
            if any(word in lowered for word in names):
                return title
        if time.monotonic() >= deadline:
            return ""
        time.sleep(_WINDOW_POLL)


def _shadow_evidence(mgr, words: tuple[str, ...], timeout: float = 1.0) -> str:
    """Title of a shadow-desktop window matching one of ``words``, or "".

    The shadow-desktop twin of :func:`_window_evidence`: the launch is reported
    the moment its window exists, instead of after a flat one-second sleep, and
    the caller's next observation is that much less likely to look at a desktop
    that has not caught up yet. Best-effort like the rest of this path - an
    enumeration failure simply returns nothing.
    """
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        try:
            titles = [w.title for w in mgr.list_windows() if w.title]
        except Exception:
            return ""
        for title in titles:
            lowered = title.lower()
            if any(word in lowered for word in words):
                return title
        if time.monotonic() >= deadline:
            return ""
        time.sleep(_WINDOW_POLL)


def _launch_report(name: str, match: "app_index.Match | None", evidence: str,
                   verb: str = "launched") -> str:
    """The result line: what was launched, how it was found, what proved it.

    ``how`` matters to the model - "resolved via Start Menu" tells it the name it
    chose was understood, while an unqualified "launched" on a name that resolved
    nowhere is the failure it used to be handed as a success.
    """
    how = f" ({app_index.describe(match)})" if match is not None else ""
    if evidence:
        return f"{verb} '{name}'{how}; window '{evidence}' is up"
    return (f"{verb} '{name}'{how}; no matching window has appeared yet - it may "
            f"still be starting, so check the screen before acting on it")


def open_app(name: str) -> str:
    """Launch (or bring up) an application by friendly name or executable.

    Resolution goes through the shell's own index first (App Paths registry +
    Start Menu shortcuts, see :mod:`jarvis.tools.app_index`): that is the list a
    user is looking at when they search the Start menu, so a name they would
    recognise resolves in one call and the entry that matched is named in the
    result. The old alias table stays underneath as the fallback.
    """
    from ..desktop import is_shadow_enabled, get_shadow_manager, ShadowDesktopManager
    from .system import open_url

    key = name.strip().lower()

    # 1. What would this name launch? The shell's own index (App Paths registry +
    #    Start Menu shortcuts) is tried first for every ordinary name: it is the
    #    list Windows search shows for that name, so it resolves an installed app
    #    in one lookup instead of the model hunting the Start menu over turns.
    match = None
    if key and key not in _SPECIAL:
        try:
            match = app_index.resolve(key)
        except Exception:
            match = None

    # 2. The classic targets, kept for what needs them: the Store Spotify, the
    #    browser executables, and the hand-written alias table.
    if key == "spotify":
        appdata = os.environ.get("APPDATA", "")
        localappdata = os.environ.get("LOCALAPPDATA", "")
        spotify_path = os.path.join(appdata, "Spotify", "Spotify.exe")
        store_spotify = os.path.join(localappdata, "Microsoft", "WindowsApps", "Spotify.exe")
        if os.path.exists(spotify_path):
            target = f'"{spotify_path}"'
        elif os.path.exists(store_spotify):
            target = f'"{store_spotify}"'
        else:
            target = "https://open.spotify.com"
    elif key in {"chrome", "google chrome"}:
        target = ShadowDesktopManager.find_browser_exe() or "chrome.exe"
    elif key in {"edge", "msedge", "microsoft edge"}:
        target = ShadowDesktopManager.find_browser_exe() or "msedge.exe"
    else:
        target = _KNOWN.get(key, name)

    # Shadow Desktop launches a *process*, so it gets the executable/alias
    # target: a Start Menu shortcut is a shell object, not something to spawn in
    # an isolated desktop.
    shadow_target = target

    if match is not None:
        target = match.target

    # Failure in the isolated workspace must never launch on the host.
    if is_shadow_enabled():
        try:
            mgr = get_shadow_manager()
            if shadow_target.startswith(("http://", "https://")):
                pid = mgr.spawn_url(shadow_target)
            else:
                pid = mgr.spawn_process(shadow_target)
            if not pid:
                return f"could not launch '{name}' in Shadow Workspace"
            _shadow_evidence(mgr, _evidence_words(name, match.name if match else ""))
            return f"launched '{name}' in Shadow Workspace"
        except Exception as exc:
            return f"could not launch '{name}' in Shadow Workspace: {exc}"

    if target.startswith(("http://", "https://")):
        return open_url(target)

    # Try to focus it first if a matching window already exists: one enumeration
    # is cheaper than a launch, and it is what the user means by "open X" when X
    # is already running.
    if focus_window(name).startswith("focused"):
        return f"focused existing '{name}'"

    evidence_words = _evidence_words(name, match.name if match else "")

    if target.startswith("ms-settings:"):
        os.startfile(target)  # type: ignore[attr-defined]
        return _launch_report(name, match, _window_evidence(evidence_words),
                              verb="opened")

    if match is not None and match.rail == "start-apps":
        # A Microsoft Store app has no executable to start: the shell opens it
        # through its AppsFolder namespace, by the application id the Start menu
        # itself uses. This is what makes "open calculator" one call instead of
        # a Start-menu search, a screenshot and a click.
        try:
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{target}"],
                             shell=False)
        except Exception as exc:
            return f"could not launch '{name}': {exc}"
        return _launch_report(name, match, _window_evidence(evidence_words))

    if match is not None:
        # A Start Menu shortcut (or an App Paths executable) launches exactly the
        # way the shell launches it - a double-click - so shell targets work
        # without a second interpretation step.
        try:
            os.startfile(target)  # type: ignore[attr-defined]
        except Exception as exc:
            return f"could not launch '{name}': {exc}"
        return _launch_report(name, match, _window_evidence(evidence_words))

    try:
        if os.name == "nt":
            # ``start`` resolves App Paths / PATH like the Run dialog does.
            subprocess.Popen(["cmd", "/c", "start", "", target], shell=False)
        else:
            subprocess.Popen([target])
        return _launch_report(name, None, _window_evidence(evidence_words))
    except Exception as exc:
        # Last resort: hand it to the shell / Start.
        try:
            os.startfile(target)  # type: ignore[attr-defined]
            return _launch_report(name, None, _window_evidence(evidence_words),
                                  verb="opened")
        except Exception:
            return f"could not launch '{name}': {exc}"


def focus_window(title: str) -> str:
    """Activate the first window whose title contains ``title``."""
    from ..desktop import is_shadow_enabled, get_shadow_manager
    if is_shadow_enabled():
        try:
            win = get_shadow_manager().find_window(title)
            if win:
                return f"focused '{win.title}' in Shadow Workspace"
            return f"no window matching '{title}' in Shadow Workspace"
        except Exception as exc:
            return f"could not focus '{title}' in Shadow Workspace: {exc}"

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
    from ..desktop import is_shadow_enabled
    if is_shadow_enabled():
        return "refused: close_window is not supported in Shadow Workspace"
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
    from ..desktop import is_shadow_enabled, get_shadow_manager
    if is_shadow_enabled():
        try:
            return [w.title for w in get_shadow_manager().list_windows() if w.title]
        except Exception:
            return []

    try:
        import pygetwindow as gw  # type: ignore

        return [w.title for w in gw.getAllWindows() if w.title.strip()]
    except Exception:
        return []



def snap_window(direction: str, title: str | None = None) -> str:
    """Position the active or named window (left, right, top, bottom, maximize, minimize, restore, center)."""
    from ..desktop import is_shadow_enabled
    if is_shadow_enabled():
        return "refused: snap_window is not supported in Shadow Workspace"
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
    from ..desktop import is_shadow_enabled
    if is_shadow_enabled():
        return "refused: tile_windows is not supported in Shadow Workspace"
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

