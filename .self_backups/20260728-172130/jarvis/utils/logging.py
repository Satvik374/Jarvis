"""Tiny colourised console logger shared across the app.

Kept dependency-free (no ``rich``) so Jarvis stays light on an 8 GB machine.
"""

from __future__ import annotations

import re
import shutil
import sys
import textwrap
import time
import unicodedata

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


# --------------------------------------------------------------------------- #
# markdown -> ANSI
#
# Models answer in markdown, so a reply arrives full of ``###``, ``**`` and
# backticks that the terminal has no reason to understand. We render a common
# subset in place. Two rules keep the surrounding panel intact:
#
#   * width is measured in PRINTABLE columns, not len() - escapes are free,
#     combining marks are free, and emoji/CJK take two columns (miscounting
#     these is what pushes a panel's right border out of line);
#   * a style is applied per WORD, so a wrap between two words can never leave
#     an escape open and bleed colour into the border.
# --------------------------------------------------------------------------- #
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

_STYLE = {
    "bold": "\033[1m",
    "italic": "\033[3m",
    "code": "\033[36m",
    "dim": "\033[2m",
    "head": "\033[1m\033[38;5;39m",
    "link": "\033[4m\033[34m",
}


# Variation selector-16/-15 and the zero-width joiner: emoji modifiers that
# occupy no column of their own. Spelled as escapes - they are invisible in an
# editor and get silently mangled by a careless copy/paste.
_ZERO_WIDTH = (chr(0xFE0F), chr(0xFE0E), chr(0x200D))


def _is_emoji(ch: str) -> bool:
    o = ord(ch)
    return (0x1F300 <= o <= 0x1FAFF or 0x1F000 <= o <= 0x1F2FF
            or 0x2600 <= o <= 0x27BF or 0x2B00 <= o <= 0x2BFF)


def _vislen(s: str) -> int:
    """Printable column width. ponytail: a ZWJ emoji sequence (family, flags)
    over-counts because each component is measured separately - switch to
    grapheme clustering only if those start showing up in replies."""
    total = 0
    for ch in _ANSI_RE.sub("", s):
        # variation selectors + ZWJ are modifiers, not columns
        if unicodedata.combining(ch) or ch in _ZERO_WIDTH:
            continue
        if unicodedata.east_asian_width(ch) in ("W", "F") or _is_emoji(ch):
            total += 2
        else:
            total += 1
    return total


def _style(text: str, *names: str) -> str:
    """Style ``text``, one word at a time. Markers are always stripped by the
    caller, so a colourless terminal still gets clean prose."""
    if not _COLORS["reset"] or not text:
        return text
    opener = "".join(_STYLE[n] for n in names)
    return " ".join(f"{opener}{word}{_COLORS['reset']}" if word else word
                    for word in text.split(" "))


_INLINE = (
    (re.compile(r"\*\*\*(.+?)\*\*\*"), ("bold", "italic")),
    (re.compile(r"\*\*(.+?)\*\*"), ("bold",)),
    (re.compile(r"__(.+?)__"), ("bold",)),
    (re.compile(r"~~(.+?)~~"), ("dim",)),
    (re.compile(r"(?<![\w*])\*(?!\s)([^*]+?)(?<!\s)\*(?![\w*])"), ("italic",)),
    (re.compile(r"(?<![\w_])_(?!\s)([^_]+?)(?<!\s)_(?![\w_])"), ("italic",)),
)
_CODE_RE = re.compile(r"`([^`]+?)`")
_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")


def _inline(text: str) -> str:
    """Render inline spans. Code first: backticks are literal inside, so a
    `**x**` in a code span must not come out bold."""
    slots: list[str] = []

    def _stash(m):
        slots.append(_style(m.group(1), "code"))
        return f"\x00{len(slots) - 1}\x00"

    text = _CODE_RE.sub(_stash, text)
    text = _LINK_RE.sub(
        lambda m: _style(m.group(1) or m.group(2), "link")
        + (" " + _style(f"({m.group(2)})", "dim") if m.group(1) else ""), text)
    for pattern, names in _INLINE:
        text = pattern.sub(lambda m, n=names: _style(m.group(1), *n), text)
    return re.sub(r"\x00(\d+)\x00", lambda m: slots[int(m.group(1))], text)


_HEAD_RE = re.compile(r"(#{1,6})\s+(.*)")
_BULLET_RE = re.compile(r"([-*+])\s+(.*)")
_NUM_RE = re.compile(r"(\d{1,3}[.)])\s+(.*)")
_RULE_RE = re.compile(r"([-*_])(\s*\1){2,}\s*$")


def render_markdown(text: str) -> list[tuple[str, int]]:
    """Markdown -> ``(rendered_line, hanging_indent)`` pairs. The indent keeps
    a wrapped bullet's continuation lined up under its own text instead of
    falling back to the left margin."""
    out: list[tuple[str, int]] = []
    in_fence = False
    for raw in str(text).splitlines() or [""]:
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue                       # the fence marker itself is noise
        if in_fence:
            out.append((_style(raw, "code"), 2))
            continue
        indent = " " * (len(raw) - len(raw.lstrip(" ")))
        if not stripped:
            out.append(("", 0))
            continue
        if _RULE_RE.match(stripped):
            out.append((_c256(_glyph("─", "-") * 24, _ARC[2]), 0))
            continue
        head = _HEAD_RE.match(stripped)
        if head:
            out.append((_style(_inline(head.group(2)), "head"), 0))
            continue
        if stripped.startswith(">"):
            body = _inline(stripped.lstrip("> ").strip())
            out.append((f"{indent}{_c(_glyph('│', '|'), 'grey')} "
                        f"{_style(body, 'dim')}", len(indent) + 2))
            continue
        bullet = _BULLET_RE.match(stripped)
        if bullet:
            dot = _c256(_glyph("•", "*"), _ARC[0])
            out.append((f"{indent}{dot} {_inline(bullet.group(2))}",
                        len(indent) + 2))
            continue
        num = _NUM_RE.match(stripped)
        if num:
            out.append((f"{indent}{_style(num.group(1), 'bold')} "
                        f"{_inline(num.group(2))}",
                        len(indent) + len(num.group(1)) + 1))
            continue
        out.append((indent + _inline(stripped), len(indent)))
    return out


def _hard_break(word: str, limit: int) -> list[str]:
    """Split one over-long word (a URL, a path) on printable width, stepping
    over ANSI escapes rather than through them."""
    chunks: list[str] = []
    cur: list[str] = []
    width, i = 0, 0
    while i < len(word):
        esc = _ANSI_RE.match(word, i)
        if esc:
            cur.append(esc.group())
            i = esc.end()
            continue
        cw = _vislen(word[i])
        if width + cw > limit and cur:
            chunks.append("".join(cur))
            cur, width = [], 0
        cur.append(word[i])
        width += cw
        i += 1
    if cur:
        chunks.append("".join(cur))
    return chunks or [""]


def _wrap(text: str, width: int, hang: int = 0) -> list[str]:
    """Word-wrap on printable width. Splitting on spaces is safe: an ANSI
    escape never contains one, so it cannot be cut in half."""
    if width < 4:
        return [text]
    pad = " " * min(hang, max(0, width - 8))
    lines: list[str] = []
    cur, cur_w = "", 0
    # `started`, not `if cur`: the first words of an indented line are empty
    # strings, and treating those as "nothing yet" ate the leading indent.
    started = False
    for word in text.split(" "):
        for piece in (_hard_break(word, width) if _vislen(word) > width
                      else [word]):
            pw = _vislen(piece)
            if started and cur_w + 1 + pw > width:
                lines.append(cur)
                cur, cur_w = pad + piece, len(pad) + pw
            elif started:
                cur, cur_w = f"{cur} {piece}", cur_w + 1 + pw
            else:
                cur, cur_w, started = piece, pw, True
    lines.append(cur)
    return lines


def jarvis(msg: str) -> None:
    """Jarvis's own replies, in a rounded panel so they stand out from the
    step-by-step log noise. Markdown in the reply is rendered, not printed
    raw."""
    tl, tr, bl, br, hz, vt = (
        ("+", "+", "+", "+", "-", "|") if _ASCII
        else ("╭", "╮", "╰", "╯", "─", "│"))
    w = min(_width() - 2, 96)              # total width incl. borders
    inner = w - 4                          # "| text |"
    lines: list[str] = []
    for rendered, hang in render_markdown(msg):
        lines.extend(_wrap(rendered, inner, hang))
    head = f"{tl}{hz * 2} JARVIS "
    top = head + hz * max(0, w - len(head) - 1) + tr
    print("\n" + _c256(top, _ARC[1]))
    side = _c256(vt, _ARC[1])
    reset = _COLORS["reset"]
    for ln in lines:
        # reset closes anything a hard-break left open; it costs no columns.
        print(f"{side} {ln}{reset}{' ' * max(0, inner - _vislen(ln))} {side}")
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
