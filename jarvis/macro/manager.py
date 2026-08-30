"""Macro Manager for Jarvis.

Stores, loads, searches, and synchronizes recorded macros with the Vector Store
and Knowledge Graph Long-Term Memory.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..utils import logging as log


@dataclass
class MacroStep:
    """A single atomic step inside a recorded Macro."""
    action: str                       # e.g. "click", "double_click", "right_click", "type", "press", "focus_window", "wait"
    args: Dict[str, Any] = field(default_factory=dict)
    description: str = ""
    delay: float = 0.2

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MacroStep:
        return cls(
            action=data.get("action", "wait"),
            args=data.get("args", {}),
            description=data.get("description", ""),
            delay=float(data.get("delay", 0.2)),
        )

    def summary(self) -> str:
        if self.action == "click":
            x, y = self.args.get("x"), self.args.get("y")
            el = self.args.get("element_name")
            target = f"'{el}' at ({x}, {y})" if el else f"({x}, {y})"
            return f"Click {target}"
        elif self.action == "double_click":
            x, y = self.args.get("x"), self.args.get("y")
            return f"Double-click ({x}, {y})"
        elif self.action == "right_click":
            x, y = self.args.get("x"), self.args.get("y")
            return f"Right-click ({x}, {y})"
        elif self.action == "type":
            txt = self.args.get("text", "")
            return f'Type "{txt}"'
        elif self.action == "press":
            keys = self.args.get("keys", "")
            return f"Press key '{keys}'"
        elif self.action == "focus_window":
            win = self.args.get("title", "")
            return f"Focus window '{win}'"
        elif self.action == "launch":
            cmd = self.args.get("command", "")
            return f"Launch '{cmd}'"
        elif self.action == "wait":
            sec = self.args.get("seconds", self.delay)
            return f"Wait {sec}s"
        return f"{self.action}({self.args})"


@dataclass
class Macro:
    """A synthesized, reusable user workflow plan."""
    name: str
    description: str
    steps: List[MacroStep] = field(default_factory=list)
    parameters: List[str] = field(default_factory=list)
    target_apps: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))
    author: str = "user"
    summary_template: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "steps": [s.to_dict() for s in self.steps],
            "parameters": self.parameters,
            "target_apps": self.target_apps,
            "created_at": self.created_at,
            "author": self.author,
            "summary_template": self.summary_template,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Macro:
        steps = [MacroStep.from_dict(s) for s in data.get("steps", [])]
        return cls(
            name=data.get("name", "unnamed_macro"),
            description=data.get("description", ""),
            steps=steps,
            parameters=data.get("parameters", []),
            target_apps=data.get("target_apps", []),
            created_at=data.get("created_at", ""),
            author=data.get("author", "user"),
            summary_template=data.get("summary_template", ""),
        )

    def format_plan(self) -> str:
        lines = [
            f"Macro: {self.name}",
            f"Description: {self.description}",
            f"Target Apps: {', '.join(self.target_apps) if self.target_apps else 'Desktop'}",
        ]
        if self.parameters:
            lines.append(f"Parameters: {', '.join(self.parameters)}")
        lines.append(f"Steps ({len(self.steps)} total):")
        for i, s in enumerate(self.steps, start=1):
            lines.append(f"  {i}. {s.summary()}")
        return "\n".join(lines)

    def format_summary(self, params: Optional[Dict[str, Any]] = None) -> str:
        """Format a natural, human-like conversational response for task completion."""
        params_dict = params or {}

        # 1. If we have a custom summary_template, use and substitute it
        template = self.summary_template.strip()
        if not template:
            template = self.description.strip()

        # Substitute parameter slots
        text = template
        for k, v in params_dict.items():
            text = text.replace(f"{{{k}}}", str(v))

        # Convert imperative commands to natural past-tense assistant statements
        return self._make_conversational(text)

    @staticmethod
    def _make_conversational(raw_text: str) -> str:
        """Convert a command description into natural Jarvis speech (e.g. 'open spotify' -> 'I have opened Spotify')."""
        if not raw_text:
            return "I have completed the task."

        text = raw_text.strip()

        # Strip leading prompt filler (e.g. "please", "can you", "jarvis", "task complete:")
        text = re.sub(r"^(?:please|can you|could you|jarvis|hey jarvis|kindly|task complete:?)\s+", "", text, flags=re.IGNORECASE).strip()

        # If it already starts with "I have" or "I've", ensure proper casing and punctuation
        if re.match(r"^(i have|i've)\b", text, re.IGNORECASE):
            text = text[0].upper() + text[1:]
            if not text.endswith((".", "!", "?")):
                text += "."
            return text

        # If it starts with a past tense verb (e.g. "opened notepad", "launched spotify"), prepend "I have "
        past_verbs = {
            "opened", "launched", "started", "played", "typed", "written", "created",
            "deleted", "closed", "switched", "focused", "found", "run", "calculated",
            "restarted", "searched", "finished", "completed"
        }
        first_word = text.split()[0].lower() if text.split() else ""
        if first_word in past_verbs:
            text = f"I have {text}"
            if not text.endswith((".", "!", "?")):
                text += "."
            return text

        # Map common action verbs to past tense conversational phrases
        verb_map = [
            (r"^open\s+and\s+play\b", "I have opened and played"),
            (r"^open\s+and\s+start\b", "I have opened and started"),
            (r"^open\s+and\s+type\b", "I have opened and typed"),
            (r"^open\s+and\s+search\b", "I have opened and searched for"),
            (r"^open\b", "I have opened"),
            (r"^launch\b", "I have launched"),
            (r"^play\b", "I have started playing"),
            (r"^search\s+for\b", "I have searched for"),
            (r"^search\b", "I have searched for"),
            (r"^type\b", "I have typed"),
            (r"^write\b", "I have written"),
            (r"^create\b", "I have created"),
            (r"^delete\b", "I have deleted"),
            (r"^close\b", "I have closed"),
            (r"^switch\s+to\b", "I have switched to"),
            (r"^focus\b", "I have focused"),
            (r"^find\b", "I have found"),
            (r"^run\b", "I have run"),
            (r"^calculate\b", "I have calculated"),
            (r"^restart\b", "I have restarted"),
        ]

        for pattern, replacement in verb_map:
            if re.search(pattern, text, re.IGNORECASE):
                text = re.sub(pattern, replacement, text, count=1, flags=re.IGNORECASE)
                break
        else:
            text = f"I have completed: {text}"

        text = text[0].upper() + text[1:]
        if not text.endswith((".", "!", "?")):
            text += "."
        return text




class MacroManager:
    """Manages storage, retrieval, and memory synchronization for Macros."""

    def __init__(self, storage_dir: Optional[Path] = None):
        if storage_dir is None:
            proj_root = Path(__file__).resolve().parent.parent.parent
            self.storage_dir = proj_root / "dataset" / "data" / "macros"
        else:
            self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def _macro_path(self, name: str) -> Path:
        slug = re.sub(r"[^\w\-]+", "_", name.strip().lower()).strip("_")
        return self.storage_dir / f"{slug}.json"

    def save_macro(self, macro: Macro, sync_memory: bool = True) -> Path:
        """Save a macro to disk and sync with Long-Term Memory (Vector + Knowledge Graph)."""
        path = self._macro_path(macro.name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(macro.to_dict(), f, indent=2, ensure_ascii=False)

        if sync_memory:
            try:
                from ..memory.manager import get_memory_manager
                mgr = get_memory_manager()

                # 1. Save as learned plan in Vector Store
                plan_dict = {
                    "name": macro.name,
                    "description": macro.description,
                    "steps": [s.summary() for s in macro.steps],
                    "target_apps": macro.target_apps,
                    "parameters": macro.parameters,
                }
                mgr.append_learned_plan(task=f"run macro {macro.name}: {macro.description}", plan=plan_dict)

                # 2. Add Knowledge Graph Triplets
                kg = mgr.knowledge_graph
                kg.add_relation(
                    source_name=f"Macro:{macro.name}",
                    relation_type="automates",
                    target_name=macro.description or macro.name,
                    context="user_macro",
                )
                for app in macro.target_apps:
                    kg.add_relation(
                        source_name=f"Macro:{macro.name}",
                        relation_type="targets",
                        target_name=app,
                        context="application",
                    )
            except Exception as exc:
                log.warn(f"Failed to sync macro '{macro.name}' to long-term memory: {exc}")

        return path

    def load_macro(self, name: str) -> Optional[Macro]:
        """Load a macro by name or slug."""
        path = self._macro_path(name)
        if not path.exists():
            # Try case-insensitive search in directory
            slug = re.sub(r"[^\w\-]+", "_", name.strip().lower()).strip("_")
            for p in self.storage_dir.glob("*.json"):
                if p.stem.lower() == slug or p.stem.lower() == name.strip().lower():
                    path = p
                    break
            else:
                return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return Macro.from_dict(data)
        except Exception as exc:
            log.error(f"Error loading macro '{name}' from {path}: {exc}")
            return None

    def list_macros(self) -> List[Macro]:
        """List all saved macros."""
        macros: List[Macro] = []
        for path in self.storage_dir.glob("*.json"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                macros.append(Macro.from_dict(data))
            except Exception:
                continue
        macros.sort(key=lambda m: m.name.lower())
        return macros

    def delete_macro(self, name: str) -> bool:
        """Delete a macro from disk and evict from long-term memory."""
        path = self._macro_path(name)
        deleted = False
        if path.exists():
            try:
                path.unlink()
                deleted = True
            except OSError:
                pass

        try:
            from ..memory.manager import get_memory_manager
            mgr = get_memory_manager()
            mgr.evict_learned_plan(f"run macro {name}")
            mgr.knowledge_graph.remove_entity(f"Macro:{name}")
        except Exception:
            pass

        return deleted

    def search_macros(self, query: str, top_k: int = 5) -> List[Tuple[Macro, float]]:
        """Find macros matching query using vector semantic search."""
        from ..memory.manager import get_memory_manager
        mgr = get_memory_manager()
        results = mgr.search_semantic(query, top_k=top_k)

        matches: List[Tuple[Macro, float]] = []
        seen_names = set()
        for rec, score in results:
            if rec.category == "learned_plan":
                for m in self.list_macros():
                    if m.name in rec.content and m.name not in seen_names:
                        matches.append((m, score))
                        seen_names.add(m.name)
        return matches

    def find_matching_macro(
        self,
        task: str,
        min_score: float = 0.80,
    ) -> Tuple[Optional[Macro], Dict[str, Any], float]:
        """Find a pre-compiled macro plan matching the task intent, with extracted parameter slots.

        Returns:
            Tuple of (Macro or None, parameters dict, confidence score 0.0 - 1.0)
        """
        task_clean = (task or "").strip().lower()
        if not task_clean:
            return None, {}, 0.0

        macros = self.list_macros()
        if not macros:
            return None, {}, 0.0

        # 1. Exact / direct slug match
        task_slug = re.sub(r"[^\w\-]+", "_", task_clean).strip("_")
        for m in macros:
            m_slug = re.sub(r"[^\w\-]+", "_", m.name.lower()).strip("_")
            if m_slug == task_slug or m.name.lower() == task_clean:
                params = self.extract_parameters(m, task)
                return m, params, 1.0

        # 2. Template / regex match against macro description / name
        for m in macros:
            desc_lower = (m.description or "").lower().strip()
            if desc_lower:
                if "{" in desc_lower and "}" in desc_lower:
                    # Convert template like "open browser and search {query}" into regex
                    regex_pat = re.escape(desc_lower)
                    regex_pat = re.sub(r'\\\{[a-zA-Z0-9_]+\\\}', r'(.+)', regex_pat)
                    if re.search(regex_pat, task_clean, re.IGNORECASE):
                        params = self.extract_parameters(m, task)
                        return m, params, 0.98

                    # Also test stripped prefix before first parameter
                    desc_prefix = desc_lower.split("{")[0].strip()
                    if len(desc_prefix) > 4 and desc_prefix in task_clean:
                        params = self.extract_parameters(m, task)
                        return m, params, 0.95
                else:
                    if desc_lower in task_clean or task_clean in desc_lower:
                        params = self.extract_parameters(m, task)
                        return m, params, 0.95

            # If task without quotes matches macro slug
            m_slug = re.sub(r"[^\w\-]+", "_", m.name.lower()).strip("_")
            if len(m_slug) > 6 and m_slug in task_slug:
                params = self.extract_parameters(m, task)
                return m, params, 0.90

        # 3. Vector Semantic Search
        try:
            semantic_matches = self.search_macros(task, top_k=3)
            if semantic_matches:
                best_macro, score = semantic_matches[0]
                if score >= min_score:
                    params = self.extract_parameters(best_macro, task)
                    return best_macro, params, float(score)
        except Exception:
            pass

        return None, {}, 0.0

    def extract_parameters(self, macro: Macro, task: str) -> Dict[str, Any]:
        """Extract parameter slot values from a task string based on macro parameters."""
        params: Dict[str, Any] = {}
        if not macro.parameters:
            return params

        quotes = re.findall(r'["\']([^"\']+)["\']', task)

        for param in macro.parameters:
            p_name = param.strip().lower()
            if p_name == "text":
                if quotes:
                    params["text"] = quotes[0]
                else:
                    type_match = re.search(r'\b(?:type|write|enter)\s+(.+?)(?:\s+in|\s+into|\s+and|$)', task, re.IGNORECASE)
                    if type_match:
                        params["text"] = type_match.group(1).strip()
            elif p_name == "filename":
                file_match = re.search(r'\b[\w\-.]+\.(?:txt|py|json|md|csv|png|jpg|pdf|docx|xlsx)\b', task, re.IGNORECASE)
                if file_match:
                    params["filename"] = file_match.group(0)
                elif quotes:
                    params["filename"] = quotes[0]
            elif p_name == "url":
                url_match = re.search(r'https?://[^\s]+', task)
                if url_match:
                    params["url"] = url_match.group(0)
            elif p_name == "query":
                q_match = re.search(r'\b(?:search|search for|google|find)\s+([a-zA-Z0-9\s]+?)(?:\s+in|\s+on|\s+using|$)', task, re.IGNORECASE)
                if q_match:
                    params["query"] = q_match.group(1).strip()
                elif quotes:
                    params["query"] = quotes[0]

        return params



_GLOBAL_MACRO_MGR: Optional[MacroManager] = None


def get_macro_manager(storage_dir: Optional[Path] = None) -> MacroManager:
    global _GLOBAL_MACRO_MGR
    if _GLOBAL_MACRO_MGR is None or storage_dir is not None:
        _GLOBAL_MACRO_MGR = MacroManager(storage_dir=storage_dir)
    return _GLOBAL_MACRO_MGR
