"""Thread-safe live telemetry and task state tracker for the Dual-Agent Architecture."""

from __future__ import annotations

import collections
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional


@dataclass
class StepRecord:
    step_num: int
    action: str
    thought: str
    args: Dict[str, Any]
    result: str = ""
    ok: bool = True
    timestamp: float = field(default_factory=time.time)


class TaskTelemetryTracker:
    """Maintains real-time thread-safe state of the Main Worker Agent."""

    def __init__(self, max_history: int = 15):
        self._lock = threading.Lock()
        self.active_task: str = ""
        self.status: str = "idle"  # idle | running | waiting_user | completed | error | cancelled
        self.start_time: float = 0.0
        self.last_update_time: float = 0.0
        self.current_plan: str = "None"
        self.plan_description: str = ""
        self.current_step: int = 0
        self.total_steps: int = 30
        self.current_action: str = ""
        self.current_thought: str = ""
        self.current_args: Dict[str, Any] = {}
        self.last_result_summary: str = ""
        self.pending_question: str = ""
        self.step_history: Deque[StepRecord] = collections.deque(maxlen=max_history)

    def reset_for_new_task(self, task: str, max_steps: int = 30) -> None:
        """Reset state for a newly dispatched task."""
        with self._lock:
            self.active_task = task.strip()
            self.status = "running"
            self.start_time = time.time()
            self.last_update_time = time.time()
            self.current_plan = "Initializing..."
            self.plan_description = ""
            self.current_step = 0
            self.total_steps = max_steps
            self.current_action = "planning"
            self.current_thought = "Analyzing requirements"
            self.current_args = {}
            self.last_result_summary = ""
            self.pending_question = ""
            self.step_history.clear()

    def update_event(self, event: Dict[str, Any]) -> None:
        """Process incoming telemetry event from Main Worker Agent."""
        with self._lock:
            self.last_update_time = time.time()
            ev_type = event.get("event", "")

            if ev_type == "plan_start":
                self.current_plan = event.get("plan_name", self.current_plan)
                self.plan_description = event.get("plan_description", "")
                self.status = "running"

            elif ev_type == "step_action":
                self.current_step = event.get("step", self.current_step)
                self.total_steps = event.get("max_steps", self.total_steps)
                self.current_action = event.get("action", "")
                self.current_thought = event.get("thought", "")
                self.current_args = event.get("args", {})
                self.status = "running"

            elif ev_type == "step_result":
                self.current_step = event.get("step", self.current_step)
                action = event.get("action", self.current_action)
                thought = event.get("thought", self.current_thought)
                args = event.get("args", self.current_args)
                result = event.get("result", "")
                ok = event.get("ok", True)

                rec = StepRecord(
                    step_num=self.current_step,
                    action=action,
                    thought=thought,
                    args=args,
                    result=result,
                    ok=ok,
                )
                self.step_history.append(rec)

            elif ev_type == "ask":
                self.pending_question = event.get("question", "")
                self.status = "waiting_user"

            elif ev_type in {"answer", "answer_received", "user_answer"}:
                self.status = "running"
                self.pending_question = ""
                ans = event.get("answer", "")
                if ans:
                    self.last_result_summary = f"User answered: {ans}"

            elif ev_type == "finish":
                self.last_result_summary = event.get("result", "Task completed.")
                self.status = "completed"

            elif ev_type == "cancelled":
                self.status = "cancelled"
                self.last_result_summary = "Task was cancelled."

            elif ev_type == "error":
                self.status = "error"
                # Supervisor completion events carry ``result`` while lower
                # level agent failures carry ``error``. Keep either useful
                # message rather than replacing it with a generic status.
                self.last_result_summary = event.get(
                    "error", event.get("result", "An error occurred.")
                )

    def get_status_summary(self) -> Dict[str, Any]:
        """Return a structured dictionary for the Communicating Agent to inspect."""
        with self._lock:
            elapsed = round(time.time() - self.start_time, 1) if self.start_time > 0 else 0.0
            recent_steps = [
                f"Step {s.step_num}: {s.action} ({'OK' if s.ok else 'Failed'}) -> {s.result[:100]}"
                for s in self.step_history
            ]
            return {
                "active_task": self.active_task,
                "status": self.status,
                "elapsed_seconds": elapsed,
                "plan": self.current_plan,
                "current_step": self.current_step,
                "total_steps": self.total_steps,
                "current_action": self.current_action,
                "current_thought": self.current_thought,
                "last_result": self.last_result_summary,
                "pending_question": self.pending_question,
                "recent_history": recent_steps[-4:],
            }

    def format_live_context_for_prompt(self) -> str:
        """Format an ultra-clean live context block for the Communicating Agent."""
        summary = self.get_status_summary()
        if summary["status"] == "idle":
            return "[MAIN AGENT STATUS: Idle. No active task running.]"

        lines = [
            f"[MAIN WORKER AGENT STATUS - {summary['status'].upper()}]",
            f"Active Task: \"{summary['active_task']}\"",
            f"Elapsed Time: {summary['elapsed_seconds']}s | Step: {summary['current_step']}/{summary['total_steps']}",
            f"Current Plan: {summary['plan']}",
            f"Current Action: {summary['current_action']}",
            f"Current Reasoning: {summary['current_thought']}",
        ]
        if summary["recent_history"]:
            lines.append("Recent Steps Executed:")
            for h in summary["recent_history"]:
                lines.append(f"  • {h}")
        if summary["pending_question"]:
            lines.append(f"⚠️ Awaiting User Input: \"{summary['pending_question']}\"")
        if summary["last_result"]:
            lines.append(f"Outcome: {summary['last_result']}")

        return "\n".join(lines)
