"""Memory manager for Jarvis.

Handles permanent memories: user facts, preferences and rules. They are kept
FOREVER - nothing here evicts a memory the user asked Jarvis to remember.
"""

from __future__ import annotations

from pathlib import Path

from ..utils import logging as log
from ..utils.paths import state_root


def get_default_memory_path() -> Path:
    return state_root() / "memory.txt"


def parse_memory_text(text: str) -> list[str]:
    """Parse memory.txt text into the list of permanent memories.

    A ``=== LEARNED TASK PLANS ===`` section, and the older unsectioned
    ``- Learned Task:`` blocks it replaced, are skipped rather than read: those
    held the agent's own learned recipes, a feature that no longer exists, and
    reading them back as facts would surface them as if the user had said them.
    They disappear from the file the next time it is rewritten.
    """
    if not text or not text.strip():
        return []

    facts: list[str] = []

    # Case 1: Structured file with explicit headers
    if "=== PERMANENT MEMORIES ===" in text or "=== LEARNED TASK PLANS ===" in text:
        if "=== PERMANENT MEMORIES ===" in text:
            perm_section = text.split("=== PERMANENT MEMORIES ===")[1]
        else:
            perm_section = text.split("=== LEARNED TASK PLANS ===")[0]
        if "=== LEARNED TASK PLANS ===" in perm_section:
            perm_section = perm_section.split("=== LEARNED TASK PLANS ===")[0]

        for line in perm_section.splitlines():
            line = line.strip()
            if line and not line.startswith("(") and not line.startswith("="):
                if line.startswith("- "):
                    line = line[2:].strip()
                if line:
                    facts.append(line)
        return facts

    # Case 2: Legacy or unsectioned memory.txt. Everything before a learned
    # block - or the whole file, when there never was one - is permanent memory.
    preamble = text.split("- Learned Task:")[0]
    for line in preamble.splitlines():
        line = line.strip()
        if line and not line.startswith("="):
            if line.startswith("- "):
                line = line[2:].strip()
            if line:
                facts.append(line)

    return facts


def format_memory_text(facts: list[str]) -> str:
    """Format permanent memories into a clean memory.txt string."""
    out = ["=== PERMANENT MEMORIES ==="]
    for f in facts:
        out.append(f"- {f.strip()}")
    if not facts:
        out.append("(No permanent facts recorded yet. Use 'remember' action to store facts/preferences.)")
    return "\n".join(out).strip() + "\n"


def remember_fact(memory_path: Path | str | None = None, fact: str = "",
                  category: str = "fact", entity: str | None = None,
                  relation: str | None = None, target_entity: str | None = None) -> str:
    """Store a fact permanently in Vector Store, Knowledge Graph, and memory.txt."""
    path = Path(memory_path) if memory_path else get_default_memory_path()
    fact_str = fact.strip()
    if not fact_str:
        return "No fact provided to remember."

    from ..memory.manager import get_memory_manager
    mgr = get_memory_manager(memory_path=path)
    return mgr.remember(
        fact=fact_str,
        category=category,
        entity=entity,
        relation=relation,
        target_entity=target_entity,
        sync_file=True,
    )


def forget_fact(memory_path: Path | str | None = None, target: str = "") -> str:
    """Remove facts matching target from Vector Store and permanent memory."""
    path = Path(memory_path) if memory_path else get_default_memory_path()
    target_str = target.strip().lower()
    if not target_str:
        return "No target provided to forget."

    from ..memory.manager import get_memory_manager
    mgr = get_memory_manager(memory_path=path)
    return mgr.forget(target=target_str, sync_file=True)

