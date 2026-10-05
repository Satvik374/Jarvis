"""Named click targets: the coordinates Jarvis has already found.

Why this exists
--------------

Finding one control is expensive. On Windows it costs a screenshot plus a UI
Automation walk; on a paired phone it costs a JPEG upload and a model round
trip. When the same control comes up again - the send button in a chat, the
play button of a player, a phone's compose icon - re-finding it repeats that
whole cost to rediscover a value that has not changed.

This module remembers the target under a NAME the agent chose, so the next use
is a lookup instead of a rediscovery:

    coordinates(action="save", name="whatsapp-send-button", kind="pc",
                x=1185, y=842, app="WhatsApp")
    click(coord="whatsapp-send-button")

Two kinds, because the two screens are unrelated coordinate spaces and mixing
them up would silently click the wrong place:

    ``pc``      this computer's desktop, used by the local pointer actions
    ``mobile``  a paired phone's screen, handed to the phone in ``remote_task``

Each entry keeps both the absolute pixel pair *and* its fraction of the screen
it was found on, so a resized window or a different phone model resolves to the
same control instead of the same pixel.

Standard library only, deliberately: the agent loop, the tool registry and the
tests all import this, and none of them should drag in a desktop dependency.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ..utils.paths import state_root

#: One file, next to the other state Jarvis keeps (memory.txt, skills).
STORE_FILENAME = "jarvis_coordinates.json"
SCHEMA_VERSION = 1

KINDS = ("pc", "mobile")
DEFAULT_KIND = "pc"

#: Retained entries. Past this the least recently used ones are dropped, so a
#: long-lived install cannot grow the file without bound.
MAX_ENTRIES = 300

#: Slugs are what the model types back, so they stay short and predictable.
MAX_NAME = 60
MAX_NOTE = 160
MAX_APP = 60

_KIND_ALIASES = {
    "pc": "pc", "desktop": "pc", "computer": "pc", "this pc": "pc",
    "windows": "pc", "local": "pc", "laptop": "pc", "mac": "pc",
    "mobile": "mobile", "phone": "mobile", "android": "mobile",
    "iphone": "mobile", "device": "mobile", "tablet": "mobile",
}

_NON_SLUG = re.compile(r"[^a-z0-9]+")
_SCREEN_RE = re.compile(r"^\s*(\d{2,5})\s*[xX*, ]\s*(\d{2,5})\s*$")


def normalize_kind(value: Any, default: str = DEFAULT_KIND) -> str:
    """Map what the model writes ("phone", "this pc") onto ``pc``/``mobile``.

    An unknown word falls back to the caller's default rather than raising: a
    mis-spelled kind must not cost a step, it must land on the screen the caller
    was already working with.
    """
    text = str(value or "").strip().lower()
    if not text:
        return default
    return _KIND_ALIASES.get(text, default if default in KINDS else DEFAULT_KIND)


def slugify(name: Any) -> str:
    """``"WhatsApp Send Button"`` -> ``"whatsapp-send-button"``."""
    return _NON_SLUG.sub("-", str(name or "").strip().lower()).strip("-")[:MAX_NAME]


def search_tokens(*texts: Any) -> tuple[str, ...]:
    """Lower-case word tokens of the given texts, for name matching."""
    out: list[str] = []
    for text in texts:
        out.extend(t for t in _NON_SLUG.split(str(text or "").lower()) if t)
    return tuple(out)


def parse_screen(value: Any) -> tuple[int, int]:
    """Read a screen size from ``"1080x2400"``, ``[1080, 2400]`` or ``(w, h)``.

    Returns ``(0, 0)`` for anything unrecognisable, which callers read as "the
    screen was not recorded" - entries without it still resolve, just at their
    stored pixels.
    """
    if value is None:
        return 0, 0
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return 0, 0
    match = _SCREEN_RE.match(str(value))
    if not match:
        return 0, 0
    try:
        return int(match.group(1)), int(match.group(2))
    except (TypeError, ValueError):
        return 0, 0


def default_store_path() -> Path:
    """Resolved per call, never at import: tests point ``state_root`` elsewhere."""
    return state_root() / STORE_FILENAME


@dataclass
class Coordinate:
    """One remembered click target."""

    name: str
    kind: str = DEFAULT_KIND
    x: int = 0
    y: int = 0
    screen_w: int = 0
    screen_h: int = 0
    app: str = ""
    note: str = ""
    source: str = "jarvis"
    created: float = 0.0
    updated: float = 0.0
    uses: int = 0
    successes: int = 0
    last_used: float = 0.0

    @property
    def slug(self) -> str:
        return slugify(self.name)

    @property
    def key(self) -> tuple[str, str]:
        return self.kind, self.slug

    def point_on(self, screen_w: int = 0, screen_h: int = 0) -> tuple[int, int]:
        """The pixel pair to click on a screen of this size.

        When the entry recorded the screen it was found on and the target screen
        differs, the stored fraction is scaled instead of the stored pixel: the
        send button sits in the same place in the window at 1280x720 as it does
        at 1920x1080, but not at the same pixel.
        """
        if (self.screen_w > 0 and self.screen_h > 0
                and screen_w > 0 and screen_h > 0
                and (self.screen_w != screen_w or self.screen_h != screen_h)):
            return (round(self.x * screen_w / self.screen_w),
                    round(self.y * screen_h / self.screen_h))
        return self.x, self.y

    def describe(self) -> str:
        """One model-facing line: name, place, and how well it has worked."""
        where = f"{self.screen_w}x{self.screen_h}" if self.screen_w and self.screen_h else "unknown screen"
        bits = [f"{self.name} [{self.kind}] @ ({self.x},{self.y}) on {where}"]
        if self.app:
            bits.append(f"app: {self.app}")
        if self.note:
            bits.append(self.note)
        if self.uses:
            bits.append(f"used {self.uses}x, {self.successes} ok")
        return "  ".join(bits)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "kind": self.kind, "x": int(self.x), "y": int(self.y),
            "screen_w": int(self.screen_w), "screen_h": int(self.screen_h),
            "app": self.app, "note": self.note, "source": self.source,
            "created": self.created, "updated": self.updated,
            "uses": int(self.uses), "successes": int(self.successes),
            "last_used": self.last_used,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Coordinate | None":
        """Rebuild an entry from disk, or ``None`` when the row is unusable."""
        name = str(data.get("name", "")).strip()
        if not name:
            return None
        try:
            x, y = int(data.get("x", 0)), int(data.get("y", 0))
        except (TypeError, ValueError):
            return None
        def _num(key: str, default: float = 0.0) -> float:
            try:
                return float(data.get(key, default) or default)
            except (TypeError, ValueError):
                return default
        def _int(key: str) -> int:
            try:
                return int(data.get(key, 0) or 0)
            except (TypeError, ValueError):
                return 0
        return cls(
            name=name,
            kind=normalize_kind(data.get("kind")),
            x=x, y=y,
            screen_w=_int("screen_w"), screen_h=_int("screen_h"),
            app=str(data.get("app", ""))[:MAX_APP],
            note=str(data.get("note", ""))[:MAX_NOTE],
            source=str(data.get("source", "jarvis")),
            created=_num("created"), updated=_num("updated"),
            uses=_int("uses"), successes=_int("successes"), last_used=_num("last_used"),
        )


def _score(entry: Coordinate, query: str) -> float:
    """How well one entry matches a search string. 0.0 means "no match".

    Deliberately generous: the agent searches in the words of the user's
    request ("whatsapp send"), while the entry was named in the words of the
    screen ("chat-composer-send-arrow"). Token overlap plus a fuzzy ratio finds
    both, and an exact slug still wins outright.
    """
    q_slug = slugify(query)
    if not q_slug:
        return 0.0
    if q_slug == entry.slug:
        return 1.0
    hay_slug = entry.slug
    if q_slug in hay_slug:
        return 0.9
    q_tokens = set(search_tokens(query))
    hay_tokens = set(search_tokens(entry.name, entry.app, entry.note))
    if q_tokens and q_tokens <= hay_tokens:
        return 0.85
    overlap = len(q_tokens & hay_tokens) / max(1, len(q_tokens))
    if overlap:
        return 0.5 + 0.3 * overlap
    return 0.4 * difflib_ratio(q_slug, hay_slug)


def difflib_ratio(a: str, b: str) -> float:
    """``SequenceMatcher`` behind a lazy import, so importing this stays cheap."""
    from difflib import SequenceMatcher

    return SequenceMatcher(None, a, b).ratio()


#: Below this a search result is noise rather than a suggestion.
MATCH_FLOOR = 0.35

#: A single search hit at least this good is treated as the target the caller
#: meant, so a half-remembered name still clicks. Two of them are never guessed
#: between - an ambiguous name resolves to nothing instead of to a wrong control.
NEAR_MATCH = 0.7


class CoordinateStore:
    """The on-disk table of remembered click targets.

    Reads are tolerant and writes are atomic: a half-written file (power loss,
    a kill mid-save) loses nothing that was already stored, and a file that
    cannot be parsed starts the table over instead of breaking every task that
    touches a coordinate.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path else None

    @property
    def path(self) -> Path:
        return self._path or default_store_path()

    # ---- storage ---------------------------------------------------------- #

    def load(self) -> list[Coordinate]:
        path = self.path
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError:
            return []
        try:
            data = json.loads(raw)
        except ValueError:
            # A corrupt file must not break the loop; it is replaced on the
            # next save. Kept as-is on disk so it can still be inspected.
            return []
        rows = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return []
        entries: list[Coordinate] = []
        for row in rows:
            if isinstance(row, dict):
                entry = Coordinate.from_dict(row)
                if entry is not None:
                    entries.append(entry)
        return entries

    def _write(self, entries: list[Coordinate]) -> None:
        path = self.path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        payload = {"version": SCHEMA_VERSION, "entries": [e.to_dict() for e in entries]}
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            temporary.write_text(json.dumps(payload, indent=1), encoding="utf-8")
            os.replace(temporary, path)
        except OSError:
            try:
                temporary.unlink()
            except OSError:
                pass

    @staticmethod
    def _trim(entries: list[Coordinate]) -> list[Coordinate]:
        if len(entries) <= MAX_ENTRIES:
            return entries
        # Keep what is actually used; forget the rest, oldest use first.
        ordered = sorted(entries, key=lambda e: (e.uses, e.last_used, e.updated))
        return ordered[len(ordered) - MAX_ENTRIES:]

    # ---- writes ----------------------------------------------------------- #

    def save(self, name: str, kind: str = DEFAULT_KIND, x: int = 0, y: int = 0,
             screen_w: int = 0, screen_h: int = 0, app: str = "",
             note: str = "", source: str = "jarvis") -> Coordinate:
        """Store (or re-store) one named target and return the saved entry.

        Saving an existing name MOVES it: the entry keeps its history but takes
        the new position, which is what makes a stale coordinate self-correcting
        the next time Jarvis clicks the control successfully.
        """
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValueError("a coordinate needs a name")
        # The slug IS the canonical name: it is what the model types back, what
        # the prompt lists, and what a lookup compares against. A name with no
        # usable characters at all (a non-Latin script, say) keeps its raw form
        # rather than collapsing to an unnamed entry.
        clean_name = slugify(clean_name) or clean_name[:MAX_NAME]
        kind = normalize_kind(kind)
        now = time.time()
        entry = Coordinate(
            name=clean_name, kind=kind, x=int(x), y=int(y),
            screen_w=int(screen_w), screen_h=int(screen_h),
            app=str(app or "")[:MAX_APP], note=str(note or "")[:MAX_NOTE],
            source=str(source or "jarvis"), created=now, updated=now,
        )
        entries = self.load()
        for index, existing in enumerate(entries):
            if existing.key == entry.key:
                entry = replace(entry, created=existing.created or now,
                                uses=existing.uses, successes=existing.successes,
                                last_used=existing.last_used)
                entries[index] = entry
                break
        else:
            entries.append(entry)
        self._write(self._trim(entries))
        return entry

    def forget(self, name: str, kind: str | None = None) -> list[Coordinate]:
        """Drop every entry the name matches; returns what was removed.

        ``kind=None`` searches both screens, so "forget the send button" removes
        the PC and the phone entry rather than silently leaving one behind.
        """
        target = slugify(name)
        if not target:
            return []
        kind = normalize_kind(kind) if kind else None
        entries = self.load()
        removed = [e for e in entries
                   if (kind is None or e.kind == kind)
                   and (e.slug == target or target in e.slug)]
        if removed:
            self._write([e for e in entries if e not in removed])
        return removed

    def record_use(self, name: str, kind: str = DEFAULT_KIND, ok: bool = True) -> None:
        """Count a click that used a remembered coordinate."""
        target = slugify(name)
        kind = normalize_kind(kind)
        entries = self.load()
        for index, entry in enumerate(entries):
            if entry.key == (kind, target):
                entries[index] = replace(
                    entry, uses=entry.uses + 1,
                    successes=entry.successes + (1 if ok else 0),
                    last_used=time.time(),
                )
                self._write(entries)
                return

    # ---- reads ------------------------------------------------------------ #

    def get(self, name: str, kind: str | None = None) -> Coordinate | None:
        """Exact-slug lookup, optionally narrowed to one screen."""
        target = slugify(name)
        if not target:
            return None
        wanted = normalize_kind(kind) if kind else None
        for entry in self.load():
            if entry.slug == target and (wanted is None or entry.kind == wanted):
                return entry
        return None

    def search(self, query: str, kind: str | None = None,
               limit: int = 5) -> list[Coordinate]:
        """Ranked matches for a name search, best first."""
        wanted = normalize_kind(kind) if kind else None
        scored: list[tuple[float, Coordinate]] = []
        for entry in self.load():
            if wanted is not None and entry.kind != wanted:
                continue
            score = _score(entry, query)
            if score >= MATCH_FLOOR:
                scored.append((score, entry))
        scored.sort(key=lambda pair: (pair[0], pair[1].uses, pair[1].last_used),
                    reverse=True)
        return [entry for _, entry in scored[:max(1, int(limit))]]

    def list_all(self, kind: str | None = None,
                 limit: int | None = None) -> list[Coordinate]:
        """Every entry, most recently used first."""
        wanted = normalize_kind(kind) if kind else None
        entries = [e for e in self.load() if wanted is None or e.kind == wanted]
        entries.sort(key=lambda e: (e.last_used or e.updated or e.created), reverse=True)
        return entries[:int(limit)] if limit else entries

    def resolve(self, name: str, kind: str = DEFAULT_KIND,
                screen_w: int = 0, screen_h: int = 0) -> Coordinate | None:
        """The entry to click for ``name`` on a screen of this size.

        An exact name wins. Otherwise a single unambiguous near match is used:
        a caller that half-remembers ``"spotify play"`` should reach the play
        button, not spend a step on a failed lookup. Two plausible candidates
        are NOT guessed between - that resolves to nothing, and the caller's
        suggestion list carries the exact names instead.
        """
        entry = self.get(name, kind=kind)
        if entry is None:
            near = [c for c in self.search(name, kind=kind, limit=5)
                    if _score(c, name) >= NEAR_MATCH]
            if len(near) == 1:
                entry = near[0]
        if entry is None:
            return None
        x, y = entry.point_on(screen_w, screen_h)
        return replace(entry, x=x, y=y) if (x, y) != (entry.x, entry.y) else entry


# --------------------------------------------------------------------------- #
# module-level helpers: the seam the registry and the loop use
# --------------------------------------------------------------------------- #

def get_store(path: Path | str | None = None) -> CoordinateStore:
    """A store on the default path (or an explicit one, for tests)."""
    return CoordinateStore(path)


def save(name: str, kind: str = DEFAULT_KIND, x: int = 0, y: int = 0,
         screen: Any = None, app: str = "", note: str = "",
         source: str = "jarvis", path: Path | str | None = None) -> Coordinate:
    screen_w, screen_h = parse_screen(screen)
    return get_store(path).save(name, kind=kind, x=x, y=y,
                                screen_w=screen_w, screen_h=screen_h,
                                app=app, note=note, source=source)


def find(query: str, kind: str | None = None, limit: int = 5,
         path: Path | str | None = None) -> list[Coordinate]:
    return get_store(path).search(query, kind=kind, limit=limit)


def look_up(name: str, kind: str | None = None,
            path: Path | str | None = None) -> Coordinate | None:
    return get_store(path).get(name, kind=kind)


def list_all(kind: str | None = None, limit: int | None = None,
             path: Path | str | None = None) -> list[Coordinate]:
    return get_store(path).list_all(kind=kind, limit=limit)


def forget(name: str, kind: str | None = None,
           path: Path | str | None = None) -> list[Coordinate]:
    return get_store(path).forget(name, kind=kind)


def note(task: str = "", window: str = "", limit: int = 4,
         path: Path | str | None = None) -> str:
    """The system-prompt block of saved coordinates this task probably needs.

    Empty when nothing matches, exactly like ``skills.note`` / ``connectors
    .note``: a prompt that carries the whole table would cost more tokens than
    the lookup it replaces, so only the entries whose name, app or note shares a
    word with the task (or the active window) are listed - and the model is told
    which action to use, both to click one and to find the rest.
    """
    haystack = search_tokens(task, window)
    if not haystack:
        return ""
    wanted = set(haystack)
    matches: list[tuple[float, Coordinate]] = []
    for entry in get_store(path).load():
        entry_tokens = set(search_tokens(entry.name, entry.app, entry.note))
        overlap = len(wanted & entry_tokens)
        if not overlap:
            continue
        score = overlap / max(1, len(entry_tokens))
        matches.append((score + min(entry.uses, 5) * 0.02, entry))
    if not matches:
        return ""
    matches.sort(key=lambda pair: pair[0], reverse=True)
    lines = ["\n\n=== COORDINATES YOU ALREADY KNOW ==="]
    for _, entry in matches[:limit]:
        lines.append(f"  {entry.describe()}")
    lines.append(
        "Click one directly - no screenshot or element search needed - with "
        "{\"action\":\"click\",\"args\":{\"coord\":\"<name>\"}} (pc) or by putting "
        "the pixel pair in a remote_task for the phone (mobile). "
        "Use coordinates(action=\"find\", query=\"...\") to search the rest, and "
        "coordinates(action=\"save\", ...) to add a target you just found - "
        "name it yourself as <app>-<control>, e.g. whatsapp-send-button.")
    lines.append("===================================")
    return "\n".join(lines)
