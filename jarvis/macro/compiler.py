"""Trajectory-to-Macro Compiler for Jarvis.

Compiles real, verified-successful agent trajectories (from TrajectoryWriter / loop.py)
into clean, optimized, reusable Macro workflow plans with dynamic slot parameters.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .manager import Macro, MacroManager, MacroStep, get_macro_manager
from ..agent.trajectory import Step, Trajectory
from ..utils import logging as log


class TrajectoryCompiler:
    """Compiles execution trajectories into optimized, parameterized Macro plans."""

    # Actions that should be filtered out from compiled macros (diagnostic / internal)
    _EXCLUDED_ACTIONS = frozenset({
        "observe",
        "take_screenshot",
        "see",
        "finish",
        "ask",
        "system_status",
        "daemon_rule",
        "hud_control",
        "self_upgrade",
        "self_heal",
        "remember",
        "forget",
        "memory_search",
        "graph_query",
    })

    def __init__(self, macro_manager: Optional[MacroManager] = None):
        self.mgr = macro_manager or get_macro_manager()

    def compile(
        self,
        trajectory: Trajectory,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Optional[Macro]:
        """Compile a successful trajectory into a clean, parameterized Macro.

        Args:
            trajectory: The recorded Trajectory object.
            name: Optional custom name for the macro (defaults to slugified task).
            description: Optional description (defaults to the task string).

        Returns:
            A compiled Macro object, or None if the trajectory contains no usable steps.
        """
        if not trajectory or not trajectory.steps:
            return None

        task_str = (trajectory.task or "").strip()
        if not task_str:
            return None

        macro_name = name or self._generate_macro_name(task_str)
        macro_desc = description or task_str

        # 1. Extract dynamic parameter slots from the original task
        slot_candidates = self._extract_task_slots(task_str)

        # 2. Filter, clean, and convert trajectory steps into MacroSteps
        raw_macro_steps: List[MacroStep] = []
        target_apps: Set[str] = set()

        for step in trajectory.steps:
            if not step.ok:
                # Exclude failed exploratory steps
                continue
            if step.action.lower() in self._EXCLUDED_ACTIONS:
                continue

            macro_step = self._convert_step(step)
            if macro_step is not None:
                raw_macro_steps.append(macro_step)
                if step.active_window and step.active_window.strip():
                    # Clean up window title to get the app name
                    app_name = self._extract_app_name_from_window(step.active_window)
                    if app_name:
                        target_apps.add(app_name)

        if not raw_macro_steps:
            return None

        # 3. Prune redundant steps (e.g. repeated waits or duplicate consecutive clicks)
        optimized_steps = self._prune_and_optimize(raw_macro_steps)

        # 4. Parameterize steps with detected slots
        parameterized_steps, used_parameters = self._parameterize_steps(
            optimized_steps, slot_candidates
        )

        # 5. Parameterize summary template if available
        summary_raw = (trajectory.summary or "").strip()
        summary_template = summary_raw
        if slot_candidates and summary_raw:
            for s_name, s_val in slot_candidates.items():
                if s_val and s_val in summary_template:
                    summary_template = summary_template.replace(s_val, f"{{{s_name}}}")
                    used_parameters.add(s_name)

        return Macro(
            name=macro_name,
            description=macro_desc,
            steps=parameterized_steps,
            parameters=list(used_parameters),
            target_apps=sorted(list(target_apps)),
            created_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            author="jarvis-compiler",
            summary_template=summary_template,
        )


    def compile_and_save(
        self,
        task: str,
        trajectory: Trajectory,
        name: Optional[str] = None,
        sync_memory: bool = True,
    ) -> Optional[Macro]:
        """Compile a trajectory and immediately persist it to disk and memory."""
        try:
            macro = self.compile(trajectory, name=name, description=task)
            if not macro or not macro.steps:
                return None

            path = self.mgr.save_macro(macro, sync_memory=sync_memory)
            log.ok(f"Compiled and saved reusable plan '{macro.name}' ({len(macro.steps)} steps) to {path.name}")
            return macro
        except Exception as exc:
            log.warn(f"Failed to compile trajectory to macro: {exc}")
            return None

    def _convert_step(self, step: Step) -> Optional[MacroStep]:
        """Convert an agent Step into a MacroStep with resolved coordinates/args."""
        action = step.action.lower()
        args = dict(step.args or {})
        desc = step.thought or f"Execute {action}"

        # Resolve element coordinates if an element id was used
        if "element" in args and isinstance(args["element"], (int, str)):
            try:
                el_id = int(args["element"])
                # Look up element in recorded step elements list
                matched_el = None
                for el in step.elements:
                    if el.get("id") == el_id:
                        matched_el = el
                        break
                if matched_el:
                    center = matched_el.get("center") or (matched_el.get("x"), matched_el.get("y"))
                    if center and len(center) == 2:
                        args["x"] = center[0]
                        args["y"] = center[1]
                    if matched_el.get("name"):
                        args["element_name"] = matched_el.get("name")
                    if matched_el.get("control_type"):
                        args["control_type"] = matched_el.get("control_type")
            except (ValueError, TypeError):
                pass

        # Map agent tools to macro actions
        if action in {"open_app", "launch"}:
            app_cmd = args.get("app") or args.get("command") or args.get("name", "")
            return MacroStep(
                action="launch",
                args={"command": app_cmd, "app": app_cmd},
                description=f"Launch application '{app_cmd}'",
                delay=1.0,
            )

        elif action in {"click", "left_click"}:
            return MacroStep(
                action="click",
                args=args,
                description=desc or "Click target",
                delay=0.3,
            )

        elif action == "double_click":
            return MacroStep(
                action="double_click",
                args=args,
                description=desc or "Double click target",
                delay=0.3,
            )

        elif action == "right_click":
            return MacroStep(
                action="right_click",
                args=args,
                description=desc or "Right click target",
                delay=0.3,
            )

        elif action == "type":
            text_val = args.get("text", "")
            return MacroStep(
                action="type",
                args={"text": text_val},
                description=f'Type "{text_val}"',
                delay=0.2,
            )

        elif action in {"press", "key", "hotkey"}:
            keys = args.get("keys") or args.get("key") or args.get("text", "")
            return MacroStep(
                action="press",
                args={"keys": keys},
                description=f"Press '{keys}'",
                delay=0.2,
            )

        elif action == "focus_window":
            title = args.get("title", "")
            return MacroStep(
                action="focus_window",
                args={"title": title},
                description=f"Focus window '{title}'",
                delay=0.3,
            )

        elif action == "wait":
            sec = float(args.get("seconds", 0.5))
            return MacroStep(
                action="wait",
                args={"seconds": sec},
                description=f"Wait {sec}s",
                delay=sec,
            )

        elif action in {"run_command", "cmd"}:
            cmd = args.get("command", "")
            return MacroStep(
                action="launch",
                args={"command": cmd},
                description=f"Run command: {cmd}",
                delay=0.5,
            )

        # Generic action fallback
        return MacroStep(
            action=action,
            args=args,
            description=desc,
            delay=0.3,
        )

    def _prune_and_optimize(self, steps: List[MacroStep]) -> List[MacroStep]:
        """Prune consecutive duplicate actions and redundant waits."""
        if not steps:
            return []

        optimized: List[MacroStep] = []
        for s in steps:
            # Check if this is a consecutive redundant focus_window on the same title
            if (
                optimized
                and s.action == "focus_window"
                and optimized[-1].action == "focus_window"
                and s.args.get("title") == optimized[-1].args.get("title")
            ):
                continue

            # Merge consecutive waits
            if (
                optimized
                and s.action == "wait"
                and optimized[-1].action == "wait"
            ):
                prev_sec = float(optimized[-1].args.get("seconds", 0.2))
                curr_sec = float(s.args.get("seconds", 0.2))
                optimized[-1].args["seconds"] = round(min(prev_sec + curr_sec, 3.0), 2)
                continue

            optimized.append(s)

        return optimized

    def _extract_task_slots(self, task: str) -> Dict[str, str]:
        """Extract candidate slot values from the task prompt.

        Finds quoted strings, URLs, file names, or queries to parameterize.
        """
        slots: Dict[str, str] = {}

        # 1. Double or single quoted strings: e.g. type "Hello World" -> slot 'text'
        quoted_matches = re.findall(r'["\']([^"\']{2,})["\']', task)
        if quoted_matches:
            slots["text"] = quoted_matches[0]

        # 2. URLs
        url_match = re.search(r'https?://[^\s]+', task)
        if url_match:
            slots["url"] = url_match.group(0)

        # 3. File names
        file_match = re.search(r'\b[\w\-.]+\.(?:txt|py|json|md|csv|png|jpg|pdf|docx|xlsx)\b', task, re.IGNORECASE)
        if file_match:
            slots["filename"] = file_match.group(0)

        # 4. Search query after 'search' or 'find'
        search_match = re.search(r'\b(?:search|search for|google|find)\s+([a-zA-Z0-9\s]+?)(?:\s+in|\s+on|\s+using|$)', task, re.IGNORECASE)
        if search_match:
            q = search_match.group(1).strip()
            if len(q) > 2 and "text" not in slots:
                slots["query"] = q

        return slots

    def _parameterize_steps(
        self,
        steps: List[MacroStep],
        slots: Dict[str, str],
    ) -> Tuple[List[MacroStep], Set[str]]:
        """Replace exact slot values in step arguments with {param_name} placeholders."""
        if not slots:
            return steps, set()

        used_params: Set[str] = set()
        param_steps: List[MacroStep] = []

        for step in steps:
            args_copy = dict(step.args)
            for arg_k, arg_v in list(args_copy.items()):
                if isinstance(arg_v, str):
                    for slot_name, slot_val in slots.items():
                        if slot_val and slot_val in arg_v:
                            args_copy[arg_k] = arg_v.replace(slot_val, f"{{{slot_name}}}")
                            used_params.add(slot_name)

            param_steps.append(
                MacroStep(
                    action=step.action,
                    args=args_copy,
                    description=step.description,
                    delay=step.delay,
                )
            )

        return param_steps, used_params

    def _generate_macro_name(self, task: str) -> str:
        """Create a clean, human-readable slug for the macro name."""
        clean = re.sub(r'["\']', '', task)
        clean = re.sub(r'[^\w\s\-]', ' ', clean)
        words = clean.strip().split()
        slug = "_".join(words[:6]).lower()
        return slug or f"macro_{int(time.time())}"

    def _extract_app_name_from_window(self, window_title: str) -> str:
        """Extract application name from window title (e.g. 'Document - Word' -> 'Word')."""
        if not window_title:
            return ""
        if " - " in window_title:
            parts = window_title.split(" - ")
            return parts[-1].strip()
        elif " — " in window_title:
            parts = window_title.split(" — ")
            return parts[-1].strip()
        return window_title.strip()


_GLOBAL_COMPILER: Optional[TrajectoryCompiler] = None


def get_trajectory_compiler(macro_manager: Optional[MacroManager] = None) -> TrajectoryCompiler:
    global _GLOBAL_COMPILER
    if _GLOBAL_COMPILER is None or macro_manager is not None:
        _GLOBAL_COMPILER = TrajectoryCompiler(macro_manager=macro_manager)
    return _GLOBAL_COMPILER
