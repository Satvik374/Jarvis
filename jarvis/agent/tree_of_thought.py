"""Tree-of-Thought (ToT) Autonomous Error Recovery & Self-Healing Engine.

Provides structured error diagnosis, multi-branch exploration trees,
automated window/process self-healing, and modality switching (UI -> Keyboard -> CLI).
"""

from __future__ import annotations

import enum
import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


class ErrorCategory(enum.Enum):
    """Categorized taxonomy of execution failures."""
    NONE = "none"
    FOCUS_LOST = "focus_lost"
    UI_NOT_FOUND = "ui_not_found"
    NO_EFFECT_OR_LOOP = "no_effect_or_loop"
    TIMEOUT_OR_HANG = "timeout_or_hang"
    SYNTAX_OR_ARG_ERROR = "syntax_or_arg_error"
    PERMISSION_OR_ACCESS = "permission_or_access"
    PROCESS_CRASHED = "process_crashed"
    OFF_SCRIPT = "off_script"
    UNKNOWN = "unknown"


class RecoveryAction(enum.Enum):
    """Actionable self-healing recovery strategies."""
    NOOP = "noop"
    REFOCUS_TARGET = "refocus_target"
    SWITCH_TO_KEYBOARD = "switch_to_keyboard"
    SWITCH_TO_CLI = "switch_to_cli"
    CLEAR_POPUP_OR_ESCAPE = "clear_popup_or_escape"
    RETRY_VISUAL_COORDINATES = "retry_visual_coordinates"
    RESTART_APPLICATION = "restart_application"
    BACKTRACK_AND_BRANCH = "backtrack_and_branch"


@dataclass
class ErrorDiagnosis:
    category: ErrorCategory
    confidence: float
    reason: str
    suggested_strategy: RecoveryAction
    advice_prompt: str


@dataclass
class ThoughtNode:
    """A node in the Tree-of-Thought search tree."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    parent_id: Optional[str] = None
    depth: int = 0
    thought: str = ""
    action: str = ""
    args: Dict[str, Any] = field(default_factory=dict)
    outcome_ok: Optional[bool] = None
    result_message: str = ""
    error_category: ErrorCategory = ErrorCategory.NONE
    score: float = 1.0
    status: str = "ACTIVE"  # ACTIVE, SUCCESS, FAILED, PRUNED, BACKTRACKED
    active_window: str = ""
    element_count: int = 0
    timestamp: float = field(default_factory=time.time)
    children: List[ThoughtNode] = field(default_factory=list)

    def add_child(self, child: ThoughtNode) -> ThoughtNode:
        child.parent_id = self.id
        child.depth = self.depth + 1
        self.children.append(child)
        return child

    def prune(self, reason: str = "") -> None:
        self.status = "PRUNED"
        self.result_message = (self.result_message + f" [PRUNED: {reason}]").strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "depth": self.depth,
            "thought": self.thought,
            "action": self.action,
            "args": self.args,
            "ok": self.outcome_ok,
            "status": self.status,
            "error": self.error_category.value,
            "score": round(self.score, 2),
            "children": [c.to_dict() for c in self.children],
        }


class ErrorClassifier:
    """Classifies action outcomes into structured diagnoses with recovery suggestions."""

    @staticmethod
    def classify(
        action: str,
        args: Dict[str, Any],
        result_message: str,
        is_ok: bool,
        screen_changed: bool,
        before_window: str,
        after_window: str,
        consecutive_repeats: int = 0,
    ) -> ErrorDiagnosis:
        msg_lower = result_message.lower()

        # 1. Success case
        if is_ok and (screen_changed or action in ("finish", "ask", "hotkey", "type_text")):
            return ErrorDiagnosis(
                category=ErrorCategory.NONE,
                confidence=1.0,
                reason="Action executed successfully.",
                suggested_strategy=RecoveryAction.NOOP,
                advice_prompt="",
            )

        # 2. Focus loss detection
        if before_window and after_window and before_window != after_window:
            if any(term in after_window.lower() for term in ("desktop", "taskbar", "program manager", "explorer")):
                return ErrorDiagnosis(
                    category=ErrorCategory.FOCUS_LOST,
                    confidence=0.9,
                    reason=f"Focus shifted away from target window '{before_window}' to '{after_window}'.",
                    suggested_strategy=RecoveryAction.REFOCUS_TARGET,
                    advice_prompt=(
                        f"SELF-HEALING NOTICE: Target window focus was lost (currently '{after_window}'). "
                        f"Refocus '{before_window}' before attempting further actions."
                    ),
                )

        # 3. Permission / Access blocked
        if any(term in msg_lower for term in ("access is denied", "permission denied", "administrator", "elevation", "uac")):
            return ErrorDiagnosis(
                category=ErrorCategory.PERMISSION_OR_ACCESS,
                confidence=0.92,
                reason="Action was blocked by Windows security or permissions.",
                suggested_strategy=RecoveryAction.SWITCH_TO_CLI,
                advice_prompt=(
                    "SELF-HEALING NOTICE: Access or permission denied. Try running via elevated CLI or alternative user path."
                ),
            )

        # 4. Timeout or freeze
        if any(term in msg_lower for term in ("timed out", "timeout", "unresponsive", "hang", "freeze")):
            return ErrorDiagnosis(
                category=ErrorCategory.TIMEOUT_OR_HANG,
                confidence=0.9,
                reason="Operation or target process timed out.",
                suggested_strategy=RecoveryAction.CLEAR_POPUP_OR_ESCAPE,
                advice_prompt=(
                    "SELF-HEALING NOTICE: Target operation timed out. Send 'escape' or 'alt+f4' to unstick dialogs, "
                    "or verify if the process is responsive."
                ),
            )

        # 5. Element not found or UI changed
        if any(term in msg_lower for term in ("not found", "element not found", "no such element", "invalid element", "element #", "could not find")):
            target_elem = args.get("element_id") or args.get("label") or "element"
            return ErrorDiagnosis(
                category=ErrorCategory.UI_NOT_FOUND,
                confidence=0.95,
                reason=f"Element '{target_elem}' was not found on screen.",
                suggested_strategy=RecoveryAction.SWITCH_TO_KEYBOARD,
                advice_prompt=(
                    f"SELF-HEALING NOTICE: UI element '{target_elem}' is missing or obscured. "
                    "Do NOT retry clicking the same missing element id. Instead: "
                    "1) Use a keyboard shortcut (e.g. 'hotkey', 'press_key', Tab, Enter), "
                    "2) Use 'click_coords' with normalized (x,y) if visible, or "
                    "3) Use a CLI command via 'run_command'."
                ),
            )

        # 6. Stuck loop / No screen change on repeat
        if consecutive_repeats >= 2 or ("screen did not change" in msg_lower and consecutive_repeats >= 1):
            return ErrorDiagnosis(
                category=ErrorCategory.NO_EFFECT_OR_LOOP,
                confidence=0.88,
                reason=f"Action '{action}' had no observable effect for {consecutive_repeats + 1} steps.",
                suggested_strategy=RecoveryAction.BACKTRACK_AND_BRANCH,
                advice_prompt=(
                    f"SELF-HEALING NOTICE: Loop detected. Repeating '{action}' is producing no progress. "
                    "PRUNE this branch immediately. Switch to an alternative modality: "
                    "try a keyboard hotkey, run a PowerShell command via 'run_command', or press 'escape' to clear popups."
                ),
            )

        # 7. Command / Script errors
        if action in ("run_command", "powershell", "cmd") or "command failed" in msg_lower or "exit code" in msg_lower or "traceback" in msg_lower:
            return ErrorDiagnosis(
                category=ErrorCategory.SYNTAX_OR_ARG_ERROR,
                confidence=0.85,
                reason=f"Command execution error: {result_message[:120]}",
                suggested_strategy=RecoveryAction.SWITCH_TO_KEYBOARD,
                advice_prompt=(
                    f"SELF-HEALING NOTICE: Command failed ({result_message[:100]}). "
                    "Fix command arguments, or achieve the goal directly through native UI/shortcuts."
                ),
            )

        # Default fallback diagnosis
        return ErrorDiagnosis(
            category=ErrorCategory.UNKNOWN if not is_ok else ErrorCategory.NONE,
            confidence=0.5,
            reason=result_message or "Unknown action outcome.",
            suggested_strategy=RecoveryAction.BACKTRACK_AND_BRANCH if not is_ok else RecoveryAction.NOOP,
            advice_prompt=(
                f"SELF-HEALING NOTICE: Action '{action}' did not succeed ({result_message[:100]}). "
                "Evaluate screen state and try an alternative action."
            ) if not is_ok else "",
        )


class TreeOfThoughtEngine:
    """Maintains the exploration tree, branch scoring, backtracking, and loop breaking."""

    def __init__(self, max_depth: int = 5, max_failures_per_branch: int = 2):
        self.root = ThoughtNode(thought="Root Task Objective", action="start")
        self.current_node = self.root
        self.max_depth = max_depth
        self.max_failures_per_branch = max_failures_per_branch
        self.node_map: Dict[str, ThoughtNode] = {self.root.id: self.root}
        self.action_history: List[Tuple[str, str, str]] = []  # (action, arg_hash, screen_win)
        self.total_heals = 0

    def record_step(
        self,
        thought: str,
        action: str,
        args: Dict[str, Any],
        is_ok: bool,
        result_message: str,
        active_window: str,
        diagnosis: ErrorDiagnosis,
    ) -> ThoughtNode:
        """Add an execution step to the active thought tree."""
        # Calculate dynamic score based on success and error category
        score = 1.0 if is_ok else max(0.1, 0.8 - (0.25 * (diagnosis.category != ErrorCategory.NONE)))
        status = "SUCCESS" if (is_ok and action == "finish") else ("ACTIVE" if is_ok else "FAILED")

        node = ThoughtNode(
            parent_id=self.current_node.id,
            thought=thought,
            action=action,
            args=args,
            outcome_ok=is_ok,
            result_message=result_message,
            error_category=diagnosis.category,
            score=score,
            status=status,
            active_window=active_window,
        )
        self.current_node.add_child(node)
        self.node_map[node.id] = node

        # Track action history for loop detection
        arg_hash = hashlib.md5(json.dumps(args, sort_keys=True).encode()).hexdigest()[:6]
        self.action_history.append((action, arg_hash, active_window))

        if is_ok:
            self.current_node = node
        else:
            # Check if this branch needs to be pruned
            failed_children = [c for c in self.current_node.children if c.outcome_ok is False]
            if len(failed_children) >= self.max_failures_per_branch:
                node.prune(f"Exceeded max branch failures ({len(failed_children)})")

        return node

    def detect_cycle(self, window_size: int = 3) -> int:
        """Returns the number of identical consecutive actions in recent history."""
        if len(self.action_history) < 2:
            return 0
        last_action = self.action_history[-1]
        repeats = 0
        for item in reversed(self.action_history[:-1]):
            if item == last_action:
                repeats += 1
            else:
                break
        return repeats

    def backtrack_to_healthy_node(self) -> Optional[ThoughtNode]:
        """Traverse up the tree to find the most recent successful ancestor."""
        curr = self.current_node
        while curr and curr.parent_id:
            if curr.outcome_ok is True and curr.status not in ("PRUNED", "FAILED"):
                return curr
            curr = self.node_map.get(curr.parent_id)
        return self.root

    def get_tree_summary(self) -> dict[str, Any]:
        """Return a structured summary of the exploration tree."""
        return {
            "root": self.root.to_dict(),
            "total_nodes": len(self.node_map),
            "total_heals": self.total_heals,
        }


class SelfHealingDirector:
    """Orchestrates error detection, self-healing repairs, and ToT guidance injection."""

    def __init__(self, config_self_healing: bool = True, max_healing_attempts: int = 3):
        self.enabled = config_self_healing
        self.max_heals = max_healing_attempts
        self.engine = TreeOfThoughtEngine()
        self.classifier = ErrorClassifier()
        self.healing_count = 0
        self.last_target_window = ""

    def diagnose_and_guide(
        self,
        thought: str,
        action: str,
        args: Dict[str, Any],
        is_ok: bool,
        result_message: str,
        screen_changed: bool,
        before_window: str,
        after_window: str,
    ) -> Tuple[ErrorDiagnosis, Optional[str]]:
        """Diagnose turn result, update ToT tree, and generate advice/interventions."""
        if not self.enabled:
            diag = ErrorDiagnosis(ErrorCategory.NONE, 1.0, "", RecoveryAction.NOOP, "")
            return diag, None

        if before_window and not self.last_target_window:
            self.last_target_window = before_window

        repeats = self.engine.detect_cycle()
        diagnosis = self.classifier.classify(
            action=action,
            args=args,
            result_message=result_message,
            is_ok=is_ok,
            screen_changed=screen_changed,
            before_window=before_window,
            after_window=after_window,
            consecutive_repeats=repeats,
        )

        # Record step in tree
        self.engine.record_step(
            thought=thought,
            action=action,
            args=args,
            is_ok=is_ok,
            result_message=result_message,
            active_window=after_window or before_window,
            diagnosis=diagnosis,
        )

        # Perform proactive self-healing if eligible
        healing_action_taken = None
        if not is_ok and self.healing_count < self.max_heals:
            if diagnosis.suggested_strategy == RecoveryAction.REFOCUS_TARGET and self.last_target_window:
                repaired = self.perform_refocus(self.last_target_window)
                if repaired:
                    self.healing_count += 1
                    self.engine.total_heals += 1
                    healing_action_taken = f"Auto-refocused target window '{self.last_target_window}'."

            elif diagnosis.suggested_strategy == RecoveryAction.CLEAR_POPUP_OR_ESCAPE:
                self.perform_escape()
                self.healing_count += 1
                self.engine.total_heals += 1
                healing_action_taken = "Sent Escape key to clear stuck dialogs/menus."

        return diagnosis, healing_action_taken

    def perform_refocus(self, window_title: str) -> bool:
        """Attempt to restore and bring the target window to the foreground."""
        try:
            import ctypes

            EnumWindows = ctypes.windll.user32.EnumWindows
            EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
            GetWindowText = ctypes.windll.user32.GetWindowTextW
            GetWindowTextLength = ctypes.windll.user32.GetWindowTextLengthW
            IsWindowVisible = ctypes.windll.user32.IsWindowVisible
            ShowWindow = ctypes.windll.user32.ShowWindow
            SetForegroundWindow = ctypes.windll.user32.SetForegroundWindow

            target_hwnd = [None]

            def foreach_window(hwnd, lParam):
                if IsWindowVisible(hwnd):
                    length = GetWindowTextLength(hwnd)
                    if length > 0:
                        buff = ctypes.create_unicode_buffer(length + 1)
                        GetWindowText(hwnd, buff, length + 1)
                        if window_title.lower() in buff.value.lower():
                            target_hwnd[0] = hwnd
                            return False
                return True

            EnumWindows(EnumWindowsProc(foreach_window), 0)

            if target_hwnd[0]:
                SW_RESTORE = 9
                ShowWindow(target_hwnd[0], SW_RESTORE)
                SetForegroundWindow(target_hwnd[0])
                time.sleep(0.2)
                return True
        except Exception:
            pass
        return False

    def perform_escape(self) -> bool:
        """Send Escape key to dismiss unexpected modal popups or dropdowns."""
        try:
            import pyautogui  # type: ignore
            pyautogui.press("escape")
            time.sleep(0.1)
            return True
        except Exception:
            return False

    def get_healing_note(self, diagnosis: ErrorDiagnosis, repair_action: Optional[str]) -> str:
        """Format an actionable instruction to append to the agent prompt."""
        parts = []
        if repair_action:
            parts.append(f"🔧 SELF-HEALING ACTION: {repair_action}")
        if diagnosis.advice_prompt:
            parts.append(diagnosis.advice_prompt)
        return " | ".join(parts) if parts else ""
