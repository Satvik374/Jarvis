"""Speculative Fast Filler Engine for Jarvis Communicating Agent.

Provides ultra-low-latency (<10ms) intent classification and contextual
conversational acknowledgment fillers so Jarvis begins speaking or responding
instantly upon task receipt while the Main Worker Agent initializes concurrently.
"""

from __future__ import annotations

import random
import re
from typing import Dict, List, Tuple


class FastFillerEngine:
    """Predicts task intent and selects contextually varied, natural acknowledgment fillers."""

    # Categorized conversational fillers (natural, executive, varied)
    _FILLERS: Dict[str, List[str]] = {
        "browser_search": [
            "Right away, searching that for you now.",
            "On it. Pulling up the browser and looking that up.",
            "Looking into that online right away, sir.",
            "Checking the web for you now.",
        ],
        "coding": [
            "Understood. Inspecting the code and preparing the solution now.",
            "Right on it. Writing and executing the script now.",
            "Taking care of that in the codebase right away.",
            "Working on that code task now, sir.",
        ],
        "app_launch": [
            "Launching that for you right away.",
            "Right on it, bringing that application up.",
            "Opening that up for you now, sir.",
            "On it. Getting that window focused.",
        ],
        "files": [
            "Handling that file operation for you now.",
            "Right away, organizing and checking those files.",
            "On it. Accessing the directory now, sir.",
            "Taking care of those files right now.",
        ],
        "system": [
            "Checking machine diagnostics and system status now.",
            "Right away, running that system check for you.",
            "Inspecting the environment right now, sir.",
        ],
        "media": [
            "Adjusting your media playback right away.",
            "Taking care of the audio controls now.",
            "Right on it, sir.",
        ],
        "general_task": [
            "Right away, setting to work on that immediately.",
            "Understood, sir. Taking care of that for you now.",
            "On it. I'll get that done and keep you updated.",
            "Right on it. Executing that task now.",
            "Consider it done, sir. Starting now.",
        ],
    }

    _INTENT_PATTERNS: List[Tuple[str, re.Pattern]] = [
        ("browser_search", re.compile(r"\b(search|google|lookup|find online|web search|youtube|url|browse|read url)\b", re.I)),
        ("coding", re.compile(r"\b(code|python|script|debug|refactor|function|program|bug|pytest|compile|html|css|javascript|database|sql)\b", re.I)),
        ("app_launch", re.compile(r"\b(open|launch|start|focus|bring up|run)\s+(notepad|calculator|calc|chrome|edge|browser|vscode|terminal|cmd|powershell|spotify|settings|explorer)\b", re.I)),
        ("files", re.compile(r"\b(file|folder|directory|download|copy|move|delete|rename|zip|unzip|convert|save to|read file|write file)\b", re.I)),
        ("system", re.compile(r"\b(system|cpu|memory|ram|battery|disk|status|diagnostics|network|ip|ping|process)\b", re.I)),
        ("media", re.compile(r"\b(volume|mute|unmute|play|pause|next track|song|music|media)\b", re.I)),
    ]

    _CONVERSATIONAL_PATTERNS = re.compile(
        r"^(hey|hello|hi|good\s+(morning|afternoon|evening)|howdy|greetings|sup|yo|what's\s+up|"
        r"how\s+are\s+you|who\s+are\s+you|what\s+can\s+you\s+do|help|tell\s+me\s+a\s+joke|"
        r"thank\s+you|thanks|goodbye|bye|see\s+you)(\s+(jarvis|there))?[.!?]*$",
        re.I
    )

    def __init__(self):
        self._last_selected: Dict[str, str] = {}

    def is_conversational(self, task: str) -> bool:
        """Check if the text is casual conversation rather than an automation task."""
        clean = (task or "").strip().lower()
        return bool(self._CONVERSATIONAL_PATTERNS.match(clean))

    def predict_intent(self, task: str) -> str:
        """Classify task into high-level intent in sub-millisecond time."""
        clean = (task or "").strip()
        if self.is_conversational(clean):
            return "conversation"
        for intent, pattern in self._INTENT_PATTERNS:
            if pattern.search(clean):
                return intent
        return "general_task"

    def get_fast_filler(self, task: str) -> str:
        """Return an instant, natural conversational fast-filler response."""
        intent = self.predict_intent(task)
        if intent == "conversation":
            return ""
        candidates = self._FILLERS.get(intent, self._FILLERS["general_task"])

        # Avoid repeating the exact same filler twice in a row for the same category
        last = self._last_selected.get(intent)
        available = [c for c in candidates if c != last] if len(candidates) > 1 else candidates
        choice = random.choice(available)
        self._last_selected[intent] = choice
        return choice


_FILLER_SINGLETON = FastFillerEngine()


def get_fast_filler(task: str) -> str:
    """Get speculative fast filler for a task string."""
    return _FILLER_SINGLETON.get_fast_filler(task)
