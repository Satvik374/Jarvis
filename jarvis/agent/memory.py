"""Memory manager for Jarvis.

Handles permanent memories (user facts, preferences, rules) and learned task plans.
Permanent memories are preserved FOREVER and are protected from eviction when
task plan history is size-capped.
"""

from __future__ import annotations

from pathlib import Path

from ..utils import logging as log


def get_default_memory_path() -> Path:
    proj_root = Path(__file__).resolve().parent.parent.parent
    return proj_root / "memory.txt"


def parse_memory_text(text: str) -> tuple[list[str], list[str]]:
    """Parse memory.txt text into (facts, learned_plans).

    facts: list of permanent memory strings (e.g. "[preference] User prefers dark mode")
    learned_plans: list of learned plan blocks starting with "- Learned Task:"
    """
    if not text or not text.strip():
        return [], []

    facts: list[str] = []
    learned_plans: list[str] = []

    # Case 1: Structured file with explicit headers
    if "=== PERMANENT MEMORIES ===" in text or "=== LEARNED TASK PLANS ===" in text:
        perm_section = ""
        plan_section = ""
        if "=== PERMANENT MEMORIES ===" in text:
            parts = text.split("=== PERMANENT MEMORIES ===")
            rest = parts[1]
            if "=== LEARNED TASK PLANS ===" in rest:
                p_parts = rest.split("=== LEARNED TASK PLANS ===")
                perm_section = p_parts[0]
                plan_section = p_parts[1]
            else:
                perm_section = rest
        elif "=== LEARNED TASK PLANS ===" in text:
            p_parts = text.split("=== LEARNED TASK PLANS ===")
            perm_section = p_parts[0]
            plan_section = p_parts[1]

        # Parse permanent facts
        for line in perm_section.splitlines():
            line = line.strip()
            if line and not line.startswith("(") and not line.startswith("="):
                if line.startswith("- "):
                    line = line[2:].strip()
                if line:
                    facts.append(line)

        # Parse learned plans
        if "- Learned Task:" in plan_section:
            plan_blocks = plan_section.split("- Learned Task:")
            for block in plan_blocks:
                block = block.strip()
                if block and not block.startswith("(") and not block.startswith("="):
                    learned_plans.append(f"- Learned Task: {block}")
        return facts, learned_plans

    # Case 2: Legacy or unsectioned memory.txt
    if "- Learned Task:" in text:
        parts = text.split("- Learned Task:")
        preamble = parts[0].strip()
        for block in parts[1:]:
            block = block.strip()
            if block:
                learned_plans.append(f"- Learned Task: {block}")
        if preamble:
            for line in preamble.splitlines():
                line = line.strip()
                if line and not line.startswith("="):
                    if line.startswith("- "):
                        line = line[2:].strip()
                    if line:
                        facts.append(line)
    else:
        # File is pure prose/facts
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("="):
                if line.startswith("- "):
                    line = line[2:].strip()
                if line:
                    facts.append(line)

    return facts, learned_plans


def format_memory_text(facts: list[str], learned_plans: list[str]) -> str:
    """Format facts and learned plans into a clean memory.txt string."""
    out = ["=== PERMANENT MEMORIES ==="]
    if facts:
        for f in facts:
            out.append(f"- {f.strip()}")
    else:
        out.append("(No permanent facts recorded yet. Use 'remember' action to store facts/preferences.)")

    out.append("\n=== LEARNED TASK PLANS ===")
    if learned_plans:
        out.append("\n\n".join(lp.strip() for lp in learned_plans))
    else:
        out.append("(No learned task plans recorded yet.)")

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


def append_learned_plan(memory_path: Path | str | None = None, task: str = "",
                        plan: dict | None = None, max_chars: int = 4000) -> None:
    """Append a verified-successful plan to Vector Store and memory.txt."""
    if not task or not plan:
        return
    path = Path(memory_path) if memory_path else get_default_memory_path()
    from ..memory.manager import get_memory_manager
    mgr = get_memory_manager(memory_path=path)
    mgr.append_learned_plan(task=task, plan=plan, max_chars=max_chars, sync_file=True)


def evict_learned_plan(memory_path: Path | str | None = None, task: str = "") -> None:
    """Evict a learned plan that failed."""
    target = task.strip().lower()
    if not target:
        return
    path = Path(memory_path) if memory_path else get_default_memory_path()
    from ..memory.manager import get_memory_manager
    mgr = get_memory_manager(memory_path=path)
    mgr.evict_learned_plan(task=target, sync_file=True)

