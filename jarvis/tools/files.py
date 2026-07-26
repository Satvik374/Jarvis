"""File-system tools, sandboxed to safe locations.

Reads are unrestricted; writes/creates are confined to the user's home tree
(or the ``allow_paths`` from config) so a hallucinated path can't clobber
system files.
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess
from pathlib import Path


def _expand(path: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(path))).resolve()


def _within(path: Path, allow: tuple[str, ...]) -> bool:
    roots = [Path.home()] + [_expand(a) for a in allow]
    return any(_is_relative_to(path, r) for r in roots)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def read_file(path: str, max_chars: int = 20000) -> str:
    p = _expand(path)
    if not p.exists():
        return f"file not found: {p}"
    if not p.is_file():
        return f"not a file: {p}"
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"could not read {p}: {exc}"
    if len(text) > max_chars:
        return text[:max_chars] + f"\n...[truncated, {len(text)} chars total]"
    return text


def write_file(path: str, content: str, allow: tuple[str, ...] = ()) -> str:
    p = _expand(path)
    if not _within(p, allow):
        return (f"refused: {p} is outside allowed write locations "
                f"(home dir + configured allow_paths)")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"wrote {len(content)} chars to {p}"
    except Exception as exc:
        return f"could not write {p}: {exc}"


def edit_file(path: str, old: str, new: str,
              allow: tuple[str, ...] = ()) -> str:
    """Replace one EXACT occurrence of ``old`` with ``new`` in a text file.
    Refuses ambiguous (multiple-match) and missing-match edits so a sloppy
    patch can never silently land in the wrong place."""
    p = _expand(path)
    if not p.is_file():
        return f"file not found: {p}"
    if not _within(p, allow):
        return (f"refused: {p} is outside allowed write locations "
                f"(home dir + configured allow_paths)")
    if not old:
        return "edit_file needs non-empty 'old' text to replace"
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"could not read {p}: {exc}"
    n = text.count(old)
    if n == 0:
        return ("no match: that exact text (including whitespace) is not in "
                f"{p.name} - read_file it and copy the text precisely")
    if n > 1:
        return (f"ambiguous: {n} matches in {p.name} - include more "
                "surrounding lines in 'old' so it is unique")
    try:
        p.write_text(text.replace(old, new, 1), encoding="utf-8")
        return f"edited {p}: replaced 1 occurrence ({len(old)} -> {len(new)} chars)"
    except Exception as exc:
        return f"could not write {p}: {exc}"


def make_dir(path: str, allow: tuple[str, ...] = ()) -> str:
    p = _expand(path)
    if not _within(p, allow):
        return (f"refused: {p} is outside allowed write locations "
                f"(home dir + configured allow_paths)")
    try:
        existed = p.is_dir()
        p.mkdir(parents=True, exist_ok=True)
        return f"folder already exists: {p}" if existed else f"created folder {p}"
    except Exception as exc:
        return f"could not create {p}: {exc}"


# Bulky machine dirs a name search should never crawl.
_SEARCH_SKIP = {"appdata", ".git", "node_modules", "__pycache__", ".venv",
                "venv", "$recycle.bin", "windows"}


def find_files(pattern: str, root: str = "~", max_results: int = 40) -> str:
    """Search for files/folders by name under ``root`` (default: home)."""
    pattern = (pattern or "").strip()
    if not pattern:
        return "find_files needs a name pattern"
    if not any(ch in pattern for ch in "*?"):
        pattern = f"*{pattern}*"          # plain word -> substring match
    r = _expand(root or "~")
    if not r.is_dir():
        return f"not a folder: {r}"
    max_results = max(1, min(200, int(max_results or 40)))
    hits: list[str] = []
    for dirpath, dirnames, filenames in os.walk(r):
        dirnames[:] = [d for d in dirnames
                       if d.lower() not in _SEARCH_SKIP]
        for name in dirnames + filenames:
            if fnmatch.fnmatch(name, pattern):
                hits.append(str(Path(dirpath) / name))
                if len(hits) >= max_results:
                    return (f"found {len(hits)}+ matches for '{pattern}' "
                            f"(stopping at {max_results}):\n" + "\n".join(hits))
    if not hits:
        return f"no matches for '{pattern}' under {r}"
    return f"found {len(hits)} match(es) for '{pattern}':\n" + "\n".join(hits)


def copy_file(src: str, dst: str, allow: tuple[str, ...] = ()) -> str:
    s, d = _expand(src), _expand(dst)
    if not s.exists():
        return f"not found: {s}"
    if d.is_dir():
        d = d / s.name
    if not _within(d, allow):     # source may be read from anywhere
        return (f"refused: {d} is outside allowed write locations "
                f"(home dir + configured allow_paths)")
    if d.exists():
        return f"refused: {d} already exists"
    try:
        d.parent.mkdir(parents=True, exist_ok=True)
        if s.is_dir():
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)
        return f"copied {s} -> {d}"
    except Exception as exc:
        return f"could not copy: {exc}"


def move_file(src: str, dst: str, allow: tuple[str, ...] = ()) -> str:
    s, d = _expand(src), _expand(dst)
    if not s.exists():
        return f"not found: {s}"
    if d.is_dir():
        d = d / s.name
    # A move changes BOTH locations, so both must be in the sandbox.
    if not (_within(s, allow) and _within(d, allow)):
        return (f"refused: move must stay inside allowed locations "
                f"(home dir + configured allow_paths)")
    if d.exists():
        return f"refused: {d} already exists"
    try:
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(s), str(d))
        return f"moved {s} -> {d}"
    except Exception as exc:
        return f"could not move: {exc}"


def delete_path(path: str, allow: tuple[str, ...] = ()) -> str:
    """Send a file or folder to the Recycle Bin (recoverable, never a hard
    delete). Path travels via an environment variable - injection-safe."""
    p = _expand(path)
    if not p.exists():
        return f"not found: {p}"
    if not _within(p, allow):
        return (f"refused: {p} is outside allowed locations "
                f"(home dir + configured allow_paths)")
    ps = ("Add-Type -AssemblyName Microsoft.VisualBasic;"
          "$p = $env:JARVIS_DEL_PATH;"
          "if (Test-Path -LiteralPath $p -PathType Container) {"
          "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory("
          "$p,'OnlyErrorDialogs','SendToRecycleBin')"
          "} else {"
          "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile("
          "$p,'OnlyErrorDialogs','SendToRecycleBin')}")
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            env=dict(os.environ, JARVIS_DEL_PATH=str(p)),
            capture_output=True, text=True, timeout=30)
    except Exception as exc:
        return f"could not delete: {exc}"
    if proc.returncode != 0 or p.exists():
        return f"delete failed: {(proc.stderr or '').strip()[:200]}"
    return f"sent to Recycle Bin: {p}"


_DL_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def download_file(url: str, dest: str = "", allow: tuple[str, ...] = (),
                  max_mb: int = 500) -> str:
    """Download a URL to disk (streamed, size-capped, sandboxed).

    Fetches any file - installer, dataset, image, PDF, archive - to ``dest``.
    ``dest`` may be a folder (the filename is taken from the URL) or a full
    path; it defaults to ~/Downloads. Writes stay inside allowed locations
    like every other file tool, and the download aborts if it exceeds
    ``max_mb`` so a runaway URL can never fill the disk.
    """
    import urllib.parse

    import requests  # type: ignore

    url = (url or "").strip()
    if not url:
        return "download_file needs a url"
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    name = os.path.basename(urllib.parse.urlparse(url).path) or "download"
    dest = (dest or "").strip()
    if not dest:
        d = _expand("~/Downloads") / name
    else:
        d = _expand(dest)
        if d.is_dir() or dest.endswith(("/", "\\")):
            d = d / name
    if not _within(d, allow):
        return (f"refused: {d} is outside allowed write locations "
                f"(home dir + configured allow_paths)")
    if d.exists():
        return f"refused: {d} already exists (move or delete it first)"
    try:
        max_bytes = max(1, int(max_mb or 500)) * 1024 * 1024
    except (TypeError, ValueError):
        max_bytes = 500 * 1024 * 1024

    total = 0
    try:
        with requests.get(url, stream=True, timeout=30,
                          headers={"User-Agent": _DL_UA}) as r:
            if r.status_code >= 400:
                return f"could not download {url}: HTTP {r.status_code}"
            d.parent.mkdir(parents=True, exist_ok=True)
            with d.open("wb") as fh:
                for chunk in r.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        fh.close()
                        d.unlink(missing_ok=True)
                        return (f"aborted: {url} exceeds the {max_mb} MB limit "
                                f"(raise max_mb to allow larger files)")
                    fh.write(chunk)
    except Exception as exc:
        d.unlink(missing_ok=True)      # don't leave a half-written file behind
        return f"could not download {url}: {exc}"
    return f"downloaded {total} bytes to {d}"


def list_dir(path: str = ".") -> str:
    p = _expand(path)
    if not p.exists():
        return f"path not found: {p}"
    if p.is_file():
        return f"{p} (file, {p.stat().st_size} bytes)"
    try:
        entries = sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
    except Exception as exc:
        return f"could not list {p}: {exc}"
    lines = []
    for e in entries[:200]:
        kind = "DIR " if e.is_dir() else "FILE"
        size = "" if e.is_dir() else f"  {e.stat().st_size}b"
        lines.append(f"{kind}  {e.name}{size}")
    header = f"{p}  ({len(entries)} entries)"
    return header + "\n" + "\n".join(lines)
