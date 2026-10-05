"""Find the app a friendly name refers to, from the data Windows Search reads.

Handing a bare name to ``cmd /c start`` is a guess: it resolves only what is on
PATH or in App Paths, so "capcut", "premiere" or "spotify" either fail outright
or open an error dialog the model has to clean up on the next turn. The Windows
search index is not exposed as a Python API, but the two things it is built from
are plainly readable:

  * **App Paths** registry keys - the table the Run dialog consults, mapping an
    executable name (``chrome.exe``) to its real install path;
  * **Start Menu shortcuts** - one ``.lnk`` per installed app, which is what the
    user is actually looking at when they type a name into the Start menu.

Reading them is one walk of a few hundred files (tens of milliseconds, cached
for a short window) and turns "which app did they mean" into a scored lookup, so
a bad guess is *refused* instead of launched. ``apps.open_app`` uses this to
resolve in one call what otherwise costs a Start-menu search, a screenshot and a
click.
"""

from __future__ import annotations

import difflib
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass

#: Below this, nothing plausibly matches - the caller should fall back to its
#: own (fuzzier) launch path rather than start something unrelated.
MIN_SCORE = 0.74

#: A resolved index is reused for this long. Short enough that a freshly
#: installed app is found within a few minutes, long enough that a task opening
#: several apps pays the walk once.
CACHE_SECONDS = 300.0

#: The shell's full Start-app roster (``Get-StartApps``, a PowerShell start of
#: ~1 s) is the slow rail, so its answer is cached for longer than the shortcut
#: walk. An empty answer is cached too: a machine without the cmdlet must not pay
#: the cost again on the next lookup.
SLOW_CACHE_SECONDS = 600.0
_UWP_TIMEOUT = 8.0

#: Start Menu entries that are never the thing a user means by an app name.
_NOT_LAUNCHABLE = (
    "uninstall", "readme", "release notes", "documentation", "docs",
    "help", "manual", "license", "website", "homepage", "support",
    "updater", "update ", "repair", "modify",
)

#: Verbs this index cannot carry out. "uninstall capcut" would happily match the
#: CapCut shortcut on a substring, and then open_app would have launched an app
#: the user asked to remove - so the request is refused instead of fuzz-matched.
_REFUSED_VERBS = frozenset({"uninstall", "remove", "uninstaller", "delete",
                            "repair", "reinstall"})

#: Words that carry no signal in an app request ("open the app please").
_FILLER = frozenset({
    "the", "a", "an", "app", "application", "program", "please", "open",
    "launch", "start", "up", "my", "for", "me", "and", "to",
})

#: Friendly names whose Start Menu label does not contain the spoken word.
#: Mapped to the stem(s) to try first, in order.
ALIASES: dict[str, tuple[str, ...]] = {
    "code": ("visual studio code", "code"),
    "vs code": ("visual studio code", "code"),
    "vscode": ("visual studio code", "code"),
    "chrome": ("google chrome", "chrome"),
    "edge": ("microsoft edge", "msedge"),
    "word": ("microsoft word", "winword"),
    "excel": ("microsoft excel", "excel"),
    "powerpoint": ("microsoft powerpoint", "powerpnt"),
    "outlook": ("microsoft outlook", "outlook"),
    "explorer": ("file explorer", "explorer"),
    "files": ("file explorer", "explorer"),
    "terminal": ("windows terminal", "wt"),
    "cmd": ("command prompt", "cmd"),
    "calculator": ("calculator", "calc"),
    "calc": ("calculator", "calc"),
    "task manager": ("task manager", "taskmgr"),
    "paint": ("paint", "mspaint"),
    "snipping tool": ("snipping tool", "snippingtool"),
    "media player": ("media player", "wmplayer"),
    "store": ("microsoft store", "store"),
    "settings": ("settings", "ms-settings"),
    "photos": ("photos", "photos"),
    "spotify": ("spotify",),
    "discord": ("discord",),
    "slack": ("slack",),
    "whatsapp": ("whatsapp",),
    "steam": ("steam",),
    "obs": ("obs studio", "obs64"),
}

#: Registry hives that can hold App Paths, key path relative to the hive root.
_APP_PATH_KEYS = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths",
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths",
)


@dataclass(frozen=True)
class Entry:
    """One launchable thing the index knows about."""

    stem: str      # what the user would type, e.g. "notepad++"
    target: str    # what to launch: an exe path, or a .lnk path
    rail: str      # how it was found: app-path | start-menu | path


@dataclass(frozen=True)
class Match:
    """A resolved app, with the reason it won (for the tool's own message)."""

    name: str      # the Start Menu / executable label that matched
    target: str
    rail: str
    score: float

    @property
    def source(self) -> str:
        return {
            "app-path": "Windows App Paths",
            "start-menu": "Start Menu",
            "start-apps": "Windows Start apps",
            "path": "PATH",
        }.get(self.rail, self.rail)


_PUNCT = re.compile(r"[^\w\s]+")
_WINDOWS = os.name == "nt"

_LOCK = threading.Lock()
_CACHE: dict[str, object] = {"entries": None, "at": 0.0,
                             "start_apps": None, "start_apps_at": 0.0}


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation and fillers: the form both sides compare in.

    A name is *typed* as well as spoken ("Premiere Pro", "the calculator"), so
    the comparison cannot be a raw string equality on what the model sent.
    """
    cleaned = _PUNCT.sub(" ", str(text or "").lower())
    words = [w for w in cleaned.split() if w and w not in _FILLER]
    return " ".join(words)


def _start_menu_dirs() -> list[str]:
    dirs = []
    for env, tail in (
        ("APPDATA", r"Microsoft\Windows\Start Menu\Programs"),
        ("PROGRAMDATA", r"Microsoft\Windows\Start Menu\Programs"),
    ):
        base = os.environ.get(env)
        if base:
            path = os.path.join(base, tail)
            if os.path.isdir(path):
                dirs.append(path)
    return dirs


def _clean_stem(stem: str) -> str:
    """Shortcut file names carry suffixes the user never speaks."""
    text = stem.strip()
    for suffix in (" - Shortcut", " (Shortcut)"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text.strip()


def _skip(stem: str) -> bool:
    lowered = stem.lower()
    return any(bad in lowered for bad in _NOT_LAUNCHABLE)


def _registry_entries() -> list[Entry]:
    """App Paths: exactly the table the Run dialog and Start search consult."""
    entries: list[Entry] = []
    try:
        import winreg  # type: ignore
    except Exception:
        return entries

    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for key_path in _APP_PATH_KEYS:
            try:
                with winreg.OpenKey(hive, key_path) as key:
                    count = winreg.QueryInfoKey(key)[0]
                    for index in range(count):
                        try:
                            name = winreg.EnumKey(key, index)
                        except OSError:
                            break
                        try:
                            with winreg.OpenKey(key, name) as sub:
                                target, _ = winreg.QueryValueEx(sub, "")
                        except OSError:
                            continue
                        if not target or not str(target).strip():
                            continue
                        stem = os.path.splitext(os.path.basename(name))[0]
                        if stem:
                            entries.append(Entry(stem, str(target), "app-path"))
            except OSError:
                continue
    return entries


def _start_menu_entries() -> list[Entry]:
    """Every Start Menu shortcut, which is the list a user's search shows."""
    entries: list[Entry] = []
    for root in _start_menu_dirs():
        for dirpath, dirnames, filenames in os.walk(root):
            # Keep the walk shallow enough to stay fast on a full Start Menu.
            depth = dirpath[len(root):].count(os.sep)
            if depth >= 3:
                dirnames[:] = []
            for filename in filenames:
                if not filename.lower().endswith((".lnk", ".exe")):
                    continue
                stem = _clean_stem(os.path.splitext(filename)[0])
                if not stem or _skip(stem):
                    continue
                entries.append(Entry(stem, os.path.join(dirpath, filename),
                                     "start-menu"))
    return entries


def _start_apps_entries() -> list[Entry]:
    """The shell's own roster, including Microsoft Store apps.

    The ``.lnk`` walk cannot see Store apps: Calculator, Photos, and the Store
    builds of WhatsApp or Spotify are registered in the shell's AppsFolder
    namespace rather than as shortcuts, so no amount of walking ``Start Menu\\
    Programs`` will find them. ``Get-StartApps`` is the shell enumerating exactly
    what the Start menu shows, AppIDs included, and it is the only roster that
    carries them - which is the whole point of resolving names here rather than
    guessing with ``start``.

    Entries are kept only when their AppID can actually be launched: a UWP
    application id (``publisher.package_hash!App``) or a real executable on
    disk. The CSIDL-relative specs classic apps report (``{GUID}\\app.exe``) are
    skipped instead of offered, because those cannot be started without the
    shell interpreting them.
    """
    if not _WINDOWS:
        return []
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-StartApps | ForEach-Object { $_.Name + [char]9 + $_.AppID }"],
            capture_output=True, text=True, timeout=_UWP_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return []

    found: list[Entry] = []
    for line in (completed.stdout or "").splitlines():
        label, _, app_id = line.partition("\t")
        stem = _clean_stem(label)
        app_id = app_id.strip()
        if not stem or _skip(stem) or not _launchable(app_id):
            continue
        found.append(Entry(stem, app_id, "start-apps"))
    return found


def _launchable(app_id: str) -> bool:
    if "!" in app_id:                       # a UWP application user model id
        return True
    return bool(app_id) and os.path.isfile(app_id)


def start_app_entries() -> list[Entry]:
    """The cached Start-app roster. Asked for only when the cheap rails miss."""
    now = time.monotonic()
    with _LOCK:
        cached = _CACHE["start_apps"]
        if cached is not None and now - float(_CACHE["start_apps_at"]) <= \
                SLOW_CACHE_SECONDS:
            return list(cached)  # type: ignore[arg-type]

    found = _start_apps_entries()
    with _LOCK:
        _CACHE["start_apps"] = found
        _CACHE["start_apps_at"] = time.monotonic()
    return list(found)


def entries() -> list[Entry]:
    """The cached index (registry first, then Start Menu). Never raises."""
    now = time.monotonic()
    with _LOCK:
        cached = _CACHE["entries"]
        if cached is not None and now - float(_CACHE["at"]) <= CACHE_SECONDS:
            return list(cached)  # type: ignore[arg-type]

    found: list[Entry] = []
    for builder in (_registry_entries, _start_menu_entries):
        try:
            found.extend(builder())
        except Exception:
            continue

    # A stem can appear on both rails; the first occurrence wins, which is the
    # registry - an app with a registered install path beats a shortcut.
    seen: set[str] = set()
    unique: list[Entry] = []
    for entry in found:
        key = _normalize(entry.stem)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(entry)

    with _LOCK:
        _CACHE["entries"] = unique
        _CACHE["at"] = time.monotonic()
    return list(unique)


def invalidate() -> None:
    """Forget the cached index (used after an install, and by tests)."""
    with _LOCK:
        _CACHE["entries"] = None
        _CACHE["at"] = 0.0
        _CACHE["start_apps"] = None
        _CACHE["start_apps_at"] = 0.0


def _score(query: str, stem: str) -> float:
    """Confidence in 0..1 that ``query`` names the app called ``stem``.

    A plain, explainable ladder rather than anything learned: the failure has to
    be readable from the tool's own message ("matched the Start Menu shortcut
    'Notepad++'"), and every branch maps to a sentence a person could write.
    """
    if not query or not stem:
        return 0.0
    if query == stem:
        return 1.0
    if stem.startswith(query):
        # A prefix stays above the substring tier however long the tail is: the
        # name *begins* with what was asked for. Longer tails are still weaker
        # answers than a shorter one, so the score decays with the extra length.
        return 0.90 - min(0.08, 0.015 * (len(stem) - len(query)))
    if query in stem:
        return 0.78
    if stem in query:
        # The request is more specific than anything on screen, e.g. "the capcut
        # editor" for a shortcut called "CapCut".
        return 0.74

    query_words = query.split()
    stem_set = set(stem.split())
    matched: list[str] = []
    for word in query_words:
        for stem_word in stem_set:
            # A word of the label starting what was asked for ("code" for
            # "codeinsiders") - but only from three characters up: two letters
            # match half the Start Menu.
            if stem_word.startswith(word) and len(word) >= 3:
                matched.append(stem_word)
                break
            # The reverse, where the label's word starts the request ("vs" for
            # "vscode"): only when it is long enough to carry signal and covers
            # a real share of the request. Measured failure: without this,
            # "instagram" matched a shortcut called "What is new in the latest
            # version" on the strength of the word "in".
            if (word.startswith(stem_word) and len(stem_word) >= 4
                    and len(stem_word) / len(word) >= 0.5):
                matched.append(stem_word)
                break
    if matched:
        # And the overlap has to be a real part of the *label*, not one short
        # word inside a sentence: "whatsapp" matched "What is new in the latest
        # version" on the word "what" until this share test was added.
        share = sum(len(word) for word in matched) / len(stem)
        if share >= 0.4:
            coverage = len(matched) / len(query_words)
            return 0.62 + 0.14 * coverage
    ratio = difflib.SequenceMatcher(None, query, stem).ratio()
    return ratio * 0.74


def _rank(query: str, pool: list[Entry]) -> list[tuple[float, Entry]]:
    scored = [
        (_score(query, _normalize(entry.stem)), entry)
        for entry in pool
    ]
    scored = [(s, e) for s, e in scored if s >= MIN_SCORE]
    # Best score first; ties go to the registry, then to the shorter stem.
    scored.sort(key=lambda pair: (
        -pair[0],
        0 if pair[1].rail == "app-path" else 1,
        len(pair[1].stem),
        pair[1].stem.lower(),
    ))
    return scored


def resolve(name: str) -> Match | None:
    """Best launch target for ``name``, or ``None`` when nothing is close.

    An alias is tried before the raw query because Windows' own labels rarely
    contain the spoken word ("vs code" -> "Visual Studio Code").
    """
    query = _normalize(name)
    if not query:
        return None
    if _REFUSED_VERBS & set(query.split()):
        return None

    # Cheap rails first: the registry and shortcut walk cost ~10 ms together and
    # answer for every classically installed app, which is nearly always enough.
    found = _best(name, query, entries())
    if found is not None:
        return found

    # Something on PATH, which ``where`` resolves the same way the shell does.
    # Checked lazily because enumerating PATH up front is wasted work here.
    if _WINDOWS:
        try:
            on_path = shutil.which(query) or shutil.which(query + ".exe")
        except Exception:
            on_path = None
        if on_path:
            return Match(os.path.splitext(os.path.basename(on_path))[0],
                         on_path, "path", 0.80)

    # Pay for the shell's roster only on a miss: it is the rail that covers
    # Microsoft Store apps, and it costs a PowerShell start to obtain.
    return _best(name, query, start_app_entries())


def _best(name: str, query: str, pool: list[Entry]) -> Match | None:
    """Best match for ``query`` (alias first) inside one pool of entries."""
    for alias in ALIASES.get(str(name or "").strip().lower(), ()):
        ranked = _rank(_normalize(alias), pool)
        if ranked:
            # The alias earns its place by *finding* the entry, not by raising the
            # confidence: an inflated score would bless a weak match (and a weak
            # match is exactly what a spoken name most needs protection from).
            score, entry = ranked[0]
            return Match(entry.stem, entry.target, entry.rail, score)

    ranked = _rank(query, pool)
    if ranked:
        score, entry = ranked[0]
        return Match(entry.stem, entry.target, entry.rail, score)
    return None


def describe(match: Match) -> str:
    """One clause for the tool result, so the model can see how it resolved."""
    return f'"{match.name}" via {match.source}'
