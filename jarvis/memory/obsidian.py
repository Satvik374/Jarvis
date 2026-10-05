"""Your Obsidian vault as Jarvis memory.

Why this exists
---------------

Jarvis already remembers what it is *told* (``remember``). Everything you have
already written down - the notes, meeting logs and journals in your Obsidian
vault - was invisible to it, so the same question got answered from scratch
while the answer sat in a markdown file.

This module makes a vault a memory **source and sink**:

* ``index()`` reads every note and files it into the existing Vector Store and
  Knowledge Graph, so vault context arrives through the Hybrid RAG block the
  agent loop already builds - retrieval, ranking and the prompt budget are
  unchanged, the notes simply became candidates;
* ``search()`` / ``read()`` answer from the vault directly, without a round trip
  through embeddings;
* ``append()`` / ``create()`` / ``daily()`` write back, so what Jarvis worked out
  lands in the notes you actually read.

Obsidian's own API is a community plugin and a remote HTTP port; a vault is just
a folder of markdown, so this reads that folder directly. Standard library only,
matching the memory subsystem it plugs into.

Two rules the writer always holds to:

* every path is resolved *inside* the vault root - a note name may not escape it
  (``../``, an absolute path, a drive letter and a UNC path are all refused);
* hidden directories are never notes, which is what keeps ``.obsidian/`` (the
  vault's own config and plugin cache) out of the index.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path
from typing import Any, Iterable

from ..utils import logging as log

#: Where the vault is. Unset means the integration is simply off, which is the
#: default for every existing install.
VAULT_ENV = "JARVIS_OBSIDIAN_VAULT"
DAILY_FOLDER_ENV = "JARVIS_OBSIDIAN_DAILY_FOLDER"
DEFAULT_DAILY_FOLDER = "Journal"

#: What one note may contribute to the prompt. Notes are long; memory is not.
MAX_NOTE_CHARS = 1200
#: Vaults can be large; these keep a single action answer bounded.
MAX_LIST = 200
MAX_SEARCH = 25
#: Refuse to walk an absurd tree rather than hang the loop on a wrong path.
MAX_NOTES = 5_000

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
_WIKILINK_RE = re.compile(r"\[\[([^\[\]|#]+)")
_FENCE_RE = re.compile(r"^\s*(```|~~~)", re.MULTILINE)
_NON_WORD = re.compile(r"[^a-z0-9]+")
#: Characters Windows refuses in a filename, plus the ones Obsidian reserves in
#: a link target (``#`` heading, ``^`` block, ``[]`` link syntax, ``|`` alias).
_ILLEGAL_IN_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f#^\[\]]+')
_TRAILING_DOTS = re.compile(r"[ .]+$")


class VaultUnavailable(RuntimeError):
    """The vault is configured but cannot be used (missing, or not a directory)."""


def vault_path_setting(cfg: Any = None) -> str:
    """The configured vault path as written, or ``""`` when the feature is off."""
    configured = ""
    memory = getattr(cfg, "memory", None) if cfg is not None else None
    if memory is not None:
        configured = str(getattr(memory, "obsidian_vault", "") or "")
    # The environment wins, here as everywhere else in the config.
    return (os.environ.get(VAULT_ENV, "").strip() or configured.strip())


def daily_folder_setting(cfg: Any = None) -> str:
    """Folder for daily notes, relative to the vault root."""
    configured = ""
    memory = getattr(cfg, "memory", None) if cfg is not None else None
    if memory is not None:
        configured = str(getattr(memory, "obsidian_daily_folder", "") or "")
    value = (os.environ.get(DAILY_FOLDER_ENV, "").strip() or configured.strip())
    return value or DEFAULT_DAILY_FOLDER


def get_vault(cfg: Any = None) -> "ObsidianVault | None":
    """Return the configured vault, ``None`` when unconfigured, else raise."""
    raw = vault_path_setting(cfg)
    if not raw:
        return None
    root = Path(raw).expanduser()
    if not root.is_dir():
        raise VaultUnavailable(
            f"{VAULT_ENV} / memory.obsidian_vault points at '{raw}', which is not a directory. "
            "Point it at the vault folder that contains your notes."
        )
    if not is_vault_root(root):
        # Not fatal - an empty vault is legitimate, notes may be added later -
        # but a folder with no markdown at all is usually the wrong folder.
        log.warn(
            f"Obsidian vault '{root}' has no markdown notes yet; it may not be the "
            "folder you meant. Notes are read from here as they are added."
        )
    return ObsidianVault(root, daily_folder=daily_folder_setting(cfg))


def is_vault_root(path: Path) -> bool:
    """True when ``path`` looks like an Obsidian vault rather than any folder."""
    return (path / ".obsidian").is_dir() or any(path.glob("*.md"))


def slugify(name: str) -> str:
    """A safe obsidian filename for a note name, keeping words readable."""
    cleaned = _ILLEGAL_IN_NAME.sub(" ", (name or "").strip())
    cleaned = " ".join(cleaned.split())
    cleaned = _TRAILING_DOTS.sub("", cleaned)
    return cleaned[:120]


@dataclass(frozen=True)
class Note:
    """One markdown file, parsed into the parts memory retrieval cares about."""

    path: str  # vault-relative, POSIX separators
    title: str
    text: str
    tags: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    links: tuple[str, ...] = ()
    modified: float = 0.0
    truncated: bool = False

    @property
    def headings(self) -> tuple[str, ...]:
        return tuple(m.group(1).strip() for m in _HEADING_RE.finditer(self.text))

    def excerpt(self, max_chars: int = MAX_NOTE_CHARS) -> str:
        """Note text without frontmatter, capped for prompt use.

        A leading H1 that only repeats the title is dropped: the title is
        already in the memory line, and memory is on a token budget.
        """
        body = _FRONTMATTER_RE.sub("", self.text).strip()
        first, newline, rest = body.partition("\n")
        if newline and first.lstrip().lstrip("#").strip().lower() == self.title.strip().lower():
            body = rest.strip()
        if len(body) <= max_chars:
            return body
        return body[:max_chars].rstrip() + " …"

    def memory_content(self, max_chars: int = MAX_NOTE_CHARS) -> str:
        """The line this note contributes to the Vector Store."""
        tag_note = f" (tags: {', '.join(self.tags)})" if self.tags else ""
        return f"[note] {self.title}{tag_note} — {self.excerpt(max_chars)} (vault: {self.path})"


def _frontmatter_lists(text: str) -> tuple[list[str], list[str]]:
    """Pull ``tags`` and ``aliases`` out of a leading YAML block.

    Hand-parsed on purpose: the two keys this needs are simple, and the memory
    subsystem must not grow a YAML dependency for them. Both the inline form
    (``tags: [a, b]`` / ``tags: a, b``) and the block form (``- a`` lines) work.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return [], []
    block = match.group(1)
    found: dict[str, list[str]] = {"tags": [], "aliases": []}
    current: str | None = None
    for raw in block.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.lstrip().startswith("-") and current and raw.startswith((" ", "\t")):
            found[current].append(line.lstrip()[1:].strip().strip("'\""))
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        if key in found:
            current = key
            inline = value.strip().strip("[]")
            if inline:
                found[key].extend(
                    part.strip().strip("'\"") for part in re.split(r"[,\s]+", inline) if part.strip()
                )
        else:
            current = None
    clean = lambda items: [i.lstrip("#").strip() for i in items if i.lstrip("#").strip()]  # noqa: E731
    return clean(found["tags"]), clean(found["aliases"])


def _title_of(path: Path, text: str) -> str:
    """Frontmatter ``title``, else the first H1, else the filename stem."""
    match = _FRONTMATTER_RE.match(text)
    if match:
        for line in match.group(1).splitlines():
            key, _, value = line.partition(":")
            if key.strip().lower() == "title" and value.strip():
                return value.strip().strip("'\"")
    for heading in _HEADING_RE.finditer(text):
        return heading.group(1).strip()
    return path.stem


def _tokens(value: str) -> list[str]:
    return [t for t in _NON_WORD.split((value or "").lower()) if t]


class ObsidianVault:
    """A directory of markdown files, read and written as Jarvis memory."""

    def __init__(self, root: Path | str, daily_folder: str = DEFAULT_DAILY_FOLDER):
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise VaultUnavailable(f"Obsidian vault '{self.root}' is not a directory.")
        self.daily_folder = (daily_folder or "").strip().strip("/\\")

    # ------------------------------------------------------------------ #
    # Names and paths
    # ------------------------------------------------------------------ #

    def _safe_target(self, name: str) -> Path:
        """Resolve a note name to a path inside the vault, or refuse it."""
        raw = (name or "").strip().strip('"').strip("'")
        if not raw:
            raise ValueError("a note name is required")
        stem = raw[:-3] if raw.lower().endswith(".md") else raw
        candidate = Path(stem)
        # ``anchor`` catches what ``is_absolute`` misses on Windows: a rooted
        # path with no drive (``/etc/passwd``) is not absolute there, but it is
        # certainly not a note name either.
        if candidate.is_absolute() or candidate.anchor:
            raise ValueError(
                f"'{name}' is an absolute path; note names are relative to the vault, "
                "e.g. 'Projects/Roadmap'"
            )
        if any(part == ".." for part in candidate.parts):
            raise ValueError(f"'{name}' leaves the vault; note names may not contain '..'")
        parts = [slugify(part) for part in candidate.parts if part not in {"", "."}]
        parts = [p for p in parts if p]
        if not parts:
            raise ValueError(f"'{name}' is not a usable note name")
        target = self.root.joinpath(*parts[:-1], f"{parts[-1]}.md")
        resolved = target.resolve()
        if self.root != resolved and self.root not in resolved.parents:
            raise ValueError(f"'{name}' escapes the vault root")
        return resolved

    def relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def exists(self, name: str) -> bool:
        return self._safe_target(name).is_file()

    # ------------------------------------------------------------------ #
    # Reading
    # ------------------------------------------------------------------ #

    def note_paths(self) -> list[Path]:
        """Every markdown note in the vault, hidden directories excluded."""
        found: list[Path] = []
        for path in self.root.rglob("*.md"):
            relative_parts = path.relative_to(self.root).parts
            if any(part.startswith(".") for part in relative_parts):
                continue  # .obsidian/, .trash/ and friends are not notes
            found.append(path)
            if len(found) >= MAX_NOTES:
                break
        return sorted(found, key=lambda p: p.as_posix().lower())

    def parse(self, path: Path) -> Note:
        text = path.read_text(encoding="utf-8", errors="replace")
        tags, aliases = _frontmatter_lists(text)
        links = tuple(
            dict.fromkeys(link.strip() for link in _WIKILINK_RE.findall(text) if link.strip())
        )
        try:
            modified = path.stat().st_mtime
        except OSError:
            modified = 0.0
        return Note(
            path=self.relative(path),
            title=_title_of(path, text),
            text=text,
            tags=tuple(tags),
            aliases=tuple(aliases),
            links=links,
            modified=modified,
            truncated=len(text) > MAX_NOTE_CHARS,
        )

    def read(self, name: str) -> Note:
        target = self._safe_target(name)
        if not target.is_file():
            raise FileNotFoundError(f"No note named '{name}' in the vault.")
        return self.parse(target)

    def notes(self, limit: int = MAX_LIST) -> list[Note]:
        """Titles and tags for the newest notes, without reading whole bodies."""
        out: list[Note] = []
        for path in self.note_paths()[: max(1, limit)]:
            try:
                out.append(self.parse(path))
            except OSError:
                continue
        return out

    def search(self, query: str, limit: int = 10) -> list[tuple[Note, float]]:
        """Rank notes by title, tag, alias, heading and body matches.

        Deliberately keyword scoring rather than embeddings: this is the path
        that answers "what did I write about X" without touching the model.
        """
        wanted = _tokens(query)
        if not wanted:
            return []
        scored: list[tuple[Note, float]] = []
        for path in self.note_paths():
            try:
                note = self.parse(path)
            except OSError:
                continue
            haystacks = {
                "title": _tokens(note.title) + _tokens(path.stem),
                "tags": _tokens(" ".join(note.tags + note.aliases)),
                "headings": _tokens(" ".join(note.headings)),
                "body": _tokens(note.excerpt(4000)),
            }
            weights = {"title": 10.0, "tags": 6.0, "headings": 4.0, "body": 1.0}
            score = 0.0
            for token in wanted:
                for field_name, words in haystacks.items():
                    hits = sum(1 for word in words if word.startswith(token) or token == word)
                    score += min(hits, 3) * weights[field_name]
            if score > 0:
                scored.append((note, score))
        scored.sort(key=lambda pair: (-pair[1], pair[0].path.lower()))
        return scored[: max(1, limit)]

    # ------------------------------------------------------------------ #
    # Writing
    # ------------------------------------------------------------------ #

    @staticmethod
    def _write(path: Path, text: str) -> None:
        """Write through a temporary sibling, so a crash cannot truncate a note."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.jarvis-tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)

    def create(self, name: str, content: str = "", tags: Iterable[str] = (),
               overwrite: bool = False) -> str:
        """Create a new note. Refuses to clobber an existing one by default."""
        target = self._safe_target(name)
        if target.exists() and not overwrite:
            raise FileExistsError(
                f"Note '{self.relative(target)}' already exists; use append to add to it, "
                "or create with overwrite."
            )
        title = slugify(target.stem) or target.stem
        front = ""
        clean_tags = [t.strip() for t in tags if str(t).strip()]
        if clean_tags:
            front = "---\ntags: [" + ", ".join(clean_tags) + "]\n---\n\n"
        body = (content or "").strip()
        self._write(target, f"{front}# {title}\n\n{body}\n" if body else f"{front}# {title}\n")
        return self.relative(target)

    def append(self, name: str, content: str, heading: str = "") -> str:
        """Add to a note, creating it if needed. Never rewrites prior text."""
        text = (content or "").strip()
        if not text:
            raise ValueError("nothing to append")
        target = self._safe_target(name)
        if not target.exists():
            self.create(name, text)
            return self.relative(target)
        existing = target.read_text(encoding="utf-8", errors="replace")
        block = f"\n\n## {heading.strip()}\n\n{text}\n" if heading.strip() else f"\n\n{text}\n"
        self._write(target, existing.rstrip("\n") + block)
        return self.relative(target)

    def daily(self, content: str, when: _date | None = None) -> str:
        """Append a bullet to today's daily note, creating the folder if needed."""
        day = when or _date.today()
        stamp = day.strftime("%Y-%m-%d")
        name = f"{self.daily_folder}/{stamp}" if self.daily_folder else stamp
        text = (content or "").strip()
        if not text:
            raise ValueError("nothing to append to the daily note")
        target = self._safe_target(name)
        bullet = "\n".join(f"- {line.strip()}" if line.strip() else "" for line in text.splitlines())
        if target.exists():
            existing = target.read_text(encoding="utf-8", errors="replace")
            self._write(target, existing.rstrip("\n") + f"\n{bullet}\n")
        else:
            self._write(target, f"# {stamp}\n\n{bullet}\n")
        return self.relative(target)

    # ------------------------------------------------------------------ #
    # Indexing into the existing memory subsystem
    # ------------------------------------------------------------------ #

    def index(self, vector_store: Any, knowledge_graph: Any = None,
              only: Iterable[str] | None = None) -> tuple[int, int]:
        """File notes into the Vector Store (and their links into the graph).

        Re-indexing updates a note in place instead of duplicating it, and notes
        deleted from the vault are pruned, so the index follows the vault.

        ``only`` limits the work to those vault-relative paths - used right after
        a write, where a full walk would cost more than the write did. Pruning is
        skipped for a partial view, which cannot know what went stale.
        """
        wanted = {p.replace("\\", "/") for p in only} if only is not None else None
        seen: set[str] = set()
        indexed = 0
        links_added = 0
        for path in self.note_paths():
            if wanted is not None and self.relative(path) not in wanted:
                continue
            try:
                note = self.parse(path)
            except OSError:
                continue
            record_id = f"note:{note.path}"
            seen.add(record_id)
            # A note's own folder is the most useful category hint it carries.
            category = note.tags[0] if note.tags else (Path(note.path).parent.as_posix()
                                                      if Path(note.path).parent.as_posix() != "." else "note")
            vector_store.add_record(
                content=note.memory_content(),
                category=category,
                doc_type="note",
                metadata={"source": "obsidian", "path": note.path, "title": note.title,
                          "tags": list(note.tags), "modified": note.modified},
                record_id=record_id,
            )
            indexed += 1
            if knowledge_graph is not None:
                for link in note.links:
                    knowledge_graph.add_relation(
                        source_name=note.title,
                        relation_type="links_to",
                        target_name=link,
                        context=f"Obsidian link in {note.path}",
                    )
                    links_added += 1

        if wanted is None:
            for stale in vector_store.get_all(doc_type="note"):
                if stale.id not in seen:
                    vector_store.delete_record(stale.id)
        return indexed, links_added

    def stats(self) -> dict[str, Any]:
        return {
            "vault": str(self.root),
            "notes": len(self.note_paths()),
            "daily_folder": self.daily_folder,
        }
