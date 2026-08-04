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
                  category: str = "fact") -> str:
    """Store a fact permanently in memory.txt so it is kept forever."""
    path = Path(memory_path) if memory_path else get_default_memory_path()
    fact_str = fact.strip()
    if not fact_str:
        return "No fact provided to remember."

    existing_text = path.read_text(encoding="utf-8") if path.exists() else ""
    facts, plans = parse_memory_text(existing_text)

    # Clean category tag
    cat = (category or "fact").strip().lower()
    entry = f"[{cat}] {fact_str}" if cat and not fact_str.startswith("[") else fact_str

    # Check if entry or fact content already exists (case insensitive)
    updated = False
    norm_fact = fact_str.lower()
    for i, f in enumerate(facts):
        f_norm = f.lower()
        if norm_fact in f_norm or f_norm in norm_fact:
            facts[i] = entry
            updated = True
            break

    if not updated:
        facts.append(entry)

    new_text = format_memory_text(facts, plans)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")
    action_type = "Updated" if updated else "Remembered"
    log.ok(f"{action_type} memory: {entry}")
    return f"{action_type} permanent memory: {entry}"


def forget_fact(memory_path: Path | str | None = None, target: str = "") -> str:
    """Remove facts matching target from permanent memory."""
    path = Path(memory_path) if memory_path else get_default_memory_path()
    target_str = target.strip().lower()
    if not target_str:
        return "No target provided to forget."

    existing_text = path.read_text(encoding="utf-8") if path.exists() else ""
    facts, plans = parse_memory_text(existing_text)

    kept_facts = []
    removed = []
    for f in facts:
        if target_str in f.lower():
            removed.append(f)
        else:
            kept_facts.append(f)

    if not removed:
        return f"No permanent memory found matching '{target}'."

    new_text = format_memory_text(kept_facts, plans)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")
    log.ok(f"Forgot memory: {removed}")
    return f"Forgot {len(removed)} memory item(s) matching '{target}': {', '.join(removed)}"


def append_learned_plan(memory_path: Path | str | None = None, task: str = "",
                        plan: dict | None = None, max_chars: int = 4000) -> None:
    """Append a verified-successful plan to memory.txt, capping plans if needed without touching permanent facts."""
    if not task or not plan:
        return
    path = Path(memory_path) if memory_path else get_default_memory_path()
    existing_text = path.read_text(encoding="utf-8") if path.exists() else ""
    facts, plans = parse_memory_text(existing_text)

    entry = (f"- Learned Task: {task.strip()}\n"
             f"  Successful Plan: {plan.get('name', '')}\n"
             f"  Approach: {plan.get('description', '')}")

    # Check deduplication
    want = task.strip().lower()
    for p in plans:
        lines = p.lower().splitlines()
        for line in lines:
            if line.strip().startswith("- learned task:"):
                stored = line.split("- learned task:")[1].strip()
                if stored and (want in stored or stored in want):
                    return  # Already learned

    plans.append(entry)

    # Size-capping: drop OLDEST learned plans if total formatted size exceeds max_chars
    new_text = format_memory_text(facts, plans)
    while len(new_text) > max_chars and len(plans) > 1:
        plans.pop(0)  # Drop oldest learned plan
        new_text = format_memory_text(facts, plans)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")
    log.ok("Saved verified-successful plan to memory.txt (learned).")


def evict_learned_plan(memory_path: Path | str | None = None, task: str = "") -> None:
    """Evict a learned plan that failed."""
    path = Path(memory_path) if memory_path else get_default_memory_path()
    target = task.strip().lower()
    if not target or not path.exists():
        return

    existing_text = path.read_text(encoding="utf-8")
    facts, plans = parse_memory_text(existing_text)

    kept_plans = []
    dropped = False
    for p in plans:
        lines = p.lower().splitlines()
        matched = False
        for line in lines:
            if line.strip().startswith("- learned task:"):
                stored = line.split("- learned task:")[1].strip()
                if stored and (target in stored or stored in target):
                    matched = True
                    break
        if matched and not dropped:
            dropped = True
            continue
        kept_plans.append(p)

    if dropped:
        new_text = format_memory_text(facts, kept_plans)
        path.write_text(new_text, encoding="utf-8")
        log.info(f"Un-learned stale plan for task: {task[:50]}")
