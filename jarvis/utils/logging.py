"""Tiny colourised console logger shared across the app.

Kept dependency-free (no ``rich``) so Jarvis stays light on an 8 GB machine.
"""

from __future__ import annotations

import shutil
import sys
import textwrap
import time

_COLORS = {
    "reset": "\033[0m", "dim": "\033[2m", "bold": "\033[1m",
    "cyan": "\033[36m", "green": "\033[32m", "yellow": "\033[33m",
    "red": "\033[31m", "magenta": "\033[35m", "blue": "\033[34m",
    "grey": "\033[90m",
}
# ASCII fallback: only true when ANSI setup failed (legacy conhost) - then
# unicode box chars won't render either, so use plain dashes.
_ASCII = False

# Force UTF-8 output where possible so ANSI + any unicode never crash the app
# on a legacy (cp1252) Windows console.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

# Enable ANSI on Windows terminals that need it.
if sys.platform == "win32":  # pragma: no cover - platform specific
    try:
        import colorama  # type: ignore

        colorama.just_fix_windows_console()
    except Exception:
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            for k in _COLORS:
                _COLORS[k] = ""
            _ASCII = True


def _c(text: str, color: str) -> str:
    return f"{_COLORS.get(color, '')}{text}{_COLORS['reset']}"


# Arc-reactor cyan->blue shades (xterm-256 indices, light to deep).
_ARC = (51, 45, 39, 33)


def _c256(text: str, n: int) -> str:
    """256-colour foreground; plain text when ANSI is unavailable."""
    if not _COLORS["reset"]:
        return text
    return f"\033[38;5;{n}m{text}\033[0m"


def _glyph(uni: str, ascii_: str) -> str:
    return ascii_ if _ASCII else uni


def _width() -> int:
    return shutil.get_terminal_size(fallback=(80, 24)).columns


def rule(label: str = "", color: str = "grey") -> None:
    """A full-width horizontal divider, optionally with a centred label."""
    label = " ".join(label.split())   # multi-line labels must not break the bar
    ch = "-" if _ASCII else "─"
    w = _width()
    if label:
        tag = f" {label} "
        side = max(0, (w - len(tag)) // 2)
        line = ch * side + tag + ch * max(0, w - side - len(tag))
    else:
        line = ch * w
    print(_c(line, color))


def _stamp() -> str:
    return _c(time.strftime("%H:%M:%S"), "dim")


def _emit(glyph: str, gcolor: str, msg: str, mcolor: str | None = None) -> None:
    """One log line: timestamp + glyph, with wrapped continuation lines
    hanging-indented so they align under the message column."""
    stamp = time.strftime("%H:%M:%S")
    pad = " " * (len(stamp) + len(glyph) + 2)
    avail = max(20, _width() - 1 - len(pad))
    out: list[str] = []
    for raw in str(msg).splitlines() or [""]:
        out.extend(textwrap.wrap(raw, avail) or [""])
    tint = (lambda t: _c(t, mcolor)) if mcolor else (lambda t: t)
    print(f"{_c(stamp, 'dim')} {_c(glyph, gcolor)} {tint(out[0])}")
    for cont in out[1:]:
        print(pad + tint(cont))


def info(msg: str) -> None:
    _emit(_glyph("•", "*"), "cyan", msg)


def step(msg: str) -> None:
    _emit(_glyph("▸", ">"), "blue", msg)


def think(msg: str) -> None:
    _emit(_glyph("~", "~"), "magenta", msg, mcolor="dim")


def act(msg: str) -> None:
    _emit(_glyph("➤", "->"), "yellow", msg)


def ok(msg: str) -> None:
    _emit(_glyph("✓", "[ok]"), "green", msg)


def warn(msg: str) -> None:
    _emit(_glyph("⚠", "[!]"), "yellow", msg, mcolor="yellow")


def error(msg: str) -> None:
    _emit(_glyph("✗", "[x]"), "red", msg, mcolor="red")


def jarvis(msg: str) -> None:
    """Jarvis's own replies, in a rounded panel so they stand out from the
    step-by-step log noise."""
    tl, tr, bl, br, hz, vt = (
        ("+", "+", "+", "+", "-", "|") if _ASCII
        else ("╭", "╮", "╰", "╯", "─", "│"))
    w = min(_width() - 2, 96)              # total width incl. borders
    inner = w - 4                          # "| text |"
    lines: list[str] = []
    for raw in str(msg).splitlines() or [""]:
        lines.extend(textwrap.wrap(raw, inner) or [""])
    head = f"{tl}{hz * 2} JARVIS "
    top = head + hz * max(0, w - len(head) - 1) + tr
    print("\n" + _c256(top, _ARC[1]))
    side = _c256(vt, _ARC[1])
    for ln in lines:
        print(f"{side} {ln.ljust(inner)} {side}")
    print(_c256(bl + hz * (w - 2) + br, _ARC[1]) + "\n")


class spinner:
    """Live one-line spinner for slow operations (model calls).

        with log.spinner("thinking"):
            brain.complete(...)

    Shows `* thinking 7s` with an animated glyph, clears itself on exit.
    No-op when stdout is not a TTY (tests, pipes) so output stays clean.
    """

    _FRAMES = "|/-\\" if _ASCII else "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, label: str = "thinking"):
        self.label = label
        self._stop = None
        self._thread = None

    def __enter__(self):
        if not sys.stdout.isatty():
            return self
        import threading
        self._stop = threading.Event()

        # glow: the glyph pulses light -> deep -> light through the arc shades
        _pulse = _ARC + _ARC[-2:0:-1]

        def _spin():
            t0, i = time.time(), 0
            while not self._stop.wait(0.12):
                frame = _c256(self._FRAMES[i % len(self._FRAMES)],
                              _pulse[i % len(_pulse)])
                line = f"  {frame} {_c(self.label, 'dim')} {_c(f'{time.time()-t0:.0f}s', 'grey')}"
                sys.stdout.write("\r" + line + "   ")
                sys.stdout.flush()
                i += 1

        self._thread = threading.Thread(target=_spin, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        if self._stop is not None:
            self._stop.set()
            self._thread.join(timeout=0.5)
            # wipe the spinner line so the next log starts clean
            sys.stdout.write("\r" + " " * (_width() - 1) + "\r")
            sys.stdout.flush()
        return False


def pop(success: bool = True) -> None:
    """Short 'task finished' sound so you know Jarvis stopped without watching.

    A rising two-note chirp on success, one low note otherwise. Best-effort:
    Windows-only (winsound is stdlib there) and never raises.
    """
    if sys.platform != "win32":
        return
    try:
        import winsound
        if success:
            winsound.Beep(880, 90)
            winsound.Beep(1320, 130)
        else:
            winsound.Beep(440, 180)
    except Exception:
        pass
