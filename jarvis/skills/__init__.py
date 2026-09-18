"""Jarvis Skills: searchable presets that tell Jarvis how to do a class of task.

See :mod:`jarvis.skills.manager` for the format, the two-stage loading (compact
index in the prompt, full body only when loaded) and the two safety properties
(a skill cannot grant a capability, and a loaded skill is scoped to one task).
"""

from .manager import (
    Skill,
    SkillError,
    SkillManager,
    all_skills,
    get_skill_manager,
    note,
    parse_skill,
    score_skill,
    slugify,
)

__all__ = [
    "Skill",
    "SkillError",
    "SkillManager",
    "all_skills",
    "get_skill_manager",
    "note",
    "parse_skill",
    "score_skill",
    "slugify",
]
