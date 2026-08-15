"""Proactive background daemon orchestrator and event dispatch engine."""

from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from ..config import Config, ROOT
from ..utils import logging as log
from .events import Event, EventRule, EventType
from .watchers import (
    BaseWatcher,
    BatteryWatcher,
    FileWatcher,
    ResourceWatcher,
    RoutineWatcher,
    WindowWatcher,
)


class ProactiveDaemon:
    """Background event-driven daemon that monitors triggers, manages rules, and executes actions."""

    def __init__(
        self,
        cfg: Optional[Config] = None,
        task_runner: Optional[Callable[[str], Any]] = None,
        rules_path: Optional[Path | str] = None,
    ):
        self.cfg = cfg or Config()
        self.task_runner = task_runner
        self.rules_path = Path(rules_path) if rules_path else (ROOT / "dataset" / "data" / "daemon_rules.json")
        self._rules: List[EventRule] = []
        self._watchers: List[BaseWatcher] = []
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self.event_history: List[Event] = []
        self.max_history = 50

        self._setup_watchers()
        self._load_rules()

    def _setup_watchers(self) -> None:
        daemon_cfg = getattr(self.cfg, "daemon", None)

        # 1. Battery Watcher
        if daemon_cfg and getattr(daemon_cfg, "battery_monitoring", True):
            low_thresh = getattr(daemon_cfg, "battery_threshold_low", 20)
            self._watchers.append(BatteryWatcher(low_threshold=low_thresh))

        # 2. Resource Watcher
        if daemon_cfg and getattr(daemon_cfg, "resource_monitoring", True):
            cpu_thresh = getattr(daemon_cfg, "cpu_threshold_high", 90)
            mem_thresh = getattr(daemon_cfg, "memory_threshold_high", 85)
            self._watchers.append(ResourceWatcher(cpu_threshold=cpu_thresh, memory_threshold=mem_thresh))

        # 3. File Watcher
        if daemon_cfg and getattr(daemon_cfg, "file_monitoring", True):
            dirs = getattr(daemon_cfg, "watch_directories", ("~/Downloads",))
            self._watchers.append(FileWatcher(directories=dirs))

        # 4. Window Watcher
        self._watchers.append(WindowWatcher())

        # 5. Routine Watcher
        if daemon_cfg:
            m_time = getattr(daemon_cfg, "morning_briefing_time", "09:00")
            m_en = getattr(daemon_cfg, "morning_briefing_enabled", True)
            e_time = getattr(daemon_cfg, "evening_summary_time", "21:00")
            e_en = getattr(daemon_cfg, "evening_summary_enabled", True)
            self._watchers.append(RoutineWatcher(
                morning_time=m_time, morning_enabled=m_en,
                evening_time=e_time, evening_enabled=e_en,
            ))

    def _default_rules(self) -> List[EventRule]:
        return [
            EventRule(
                id="default_battery_low",
                name="Low Battery Alert",
                trigger_type=EventType.BATTERY_LOW,
                condition={},
                action_type="notify",
                action_target="Battery is low. Please connect the power adapter.",
                cooldown_seconds=600.0,
            ),
            EventRule(
                id="default_high_memory",
                name="High Memory Notification",
                trigger_type=EventType.HIGH_MEMORY,
                condition={},
                action_type="notify",
                action_target="High system memory usage detected. Consider closing unused applications.",
                cooldown_seconds=900.0,
            ),
            EventRule(
                id="default_morning_briefing",
                name="Morning Routine Briefing",
                trigger_type=EventType.MORNING_ROUTINE,
                condition={},
                action_type="notify",
                action_target="Good morning, sir. System status is nominal. Ready for your daily agenda.",
                cooldown_seconds=43200.0,
            ),
        ]

    # -- Rule Management ---------------------------------------------------- #
    def add_rule(self, rule: EventRule) -> EventRule:
        with self._lock:
            # Replace if duplicate id
            self._rules = [r for r in self._rules if r.id != rule.id]
            self._rules.append(rule)
            self._save_rules()
        return rule

    def remove_rule(self, rule_id: str) -> bool:
        with self._lock:
            before = len(self._rules)
            self._rules = [r for r in self._rules if r.id != rule_id]
            if len(self._rules) != before:
                self._save_rules()
                return True
        return False

    def get_rule(self, rule_id: str) -> Optional[EventRule]:
        with self._lock:
            for r in self._rules:
                if r.id == rule_id:
                    return r
        return None

    def list_rules(self) -> List[EventRule]:
        with self._lock:
            return list(self._rules)

    def enable_rule(self, rule_id: str, enabled: bool = True) -> bool:
        with self._lock:
            rule = self.get_rule(rule_id)
            if rule:
                rule.enabled = enabled
                self._save_rules()
                return True
        return False

    # -- Event Processing -------------------------------------------------- #
    def process_event(self, event: Event, now: float | None = None) -> List[EventRule]:
        """Evaluate rules against the given event and fire matching actions."""
        current_time = now if now is not None else time.time()
        triggered_rules: List[EventRule] = []

        with self._lock:
            self.event_history.append(event)
            if len(self.event_history) > self.max_history:
                self.event_history.pop(0)

            for rule in self._rules:
                if rule.matches(event, current_time):
                    rule.mark_triggered(current_time)
                    triggered_rules.append(rule)

            if triggered_rules:
                self._save_rules()

        # Execute triggered actions outside lock
        for rule in triggered_rules:
            self._dispatch_action(rule, event)

        return triggered_rules

    def _dispatch_action(self, rule: EventRule, event: Event) -> None:
        log.info(f"⏰ [PROACTIVE EVENT] {rule.name} fired: {event.message}")

        # 1. UI Web Event broadcast
        try:
            from ..browser_worker import emit
            emit("proactive_alert",
                 title=event.title or rule.name,
                 message=event.message or rule.action_target,
                 event_type=event.type.value,
                 rule_name=rule.name,
                 timestamp=event.timestamp)
        except Exception:
            pass

        # 2. Voice announcement (if enabled)
        if getattr(getattr(self.cfg, "daemon", None), "voice_announcements", True) and getattr(self.cfg, "voice_enabled", False):
            try:
                from ..utils import voice
                speak_text = rule.action_target if rule.action_type == "notify" else event.message
                if speak_text:
                    threading.Thread(target=voice.speak, args=(speak_text,), daemon=True).start()
            except Exception:
                pass

        # 3. Task Execution
        if rule.action_type == "task" and self.task_runner:
            def _run_task():
                from ..scheduler import desktop
                with desktop():
                    try:
                        self.task_runner(rule.action_target)
                    except Exception as exc:
                        log.warn(f"Proactive task execution error ({rule.name}): {exc}")

            threading.Thread(target=_run_task, daemon=True, name=f"jarvis-proactive-{rule.id}").start()

        # 4. Macro Execution
        elif rule.action_type == "macro":
            def _run_macro():
                from ..scheduler import desktop
                with desktop():
                    try:
                        from ..macro.manager import get_macro_manager
                        get_macro_manager().replay_macro(rule.action_target)
                    except Exception as exc:
                        log.warn(f"Proactive macro replay error ({rule.name}): {exc}")

            threading.Thread(target=_run_macro, daemon=True, name=f"jarvis-proactive-macro-{rule.id}").start()

    def tick(self, now: float | None = None) -> List[Event]:
        """Perform one poll tick across all active watchers."""
        current_time = now if now is not None else time.time()
        discovered_events: List[Event] = []

        for watcher in self._watchers:
            try:
                events = watcher.check(current_time)
                for ev in events:
                    discovered_events.append(ev)
                    self.process_event(ev, current_time)
            except Exception as exc:
                log.warn(f"Watcher {watcher.name} tick error: {exc}")

        return discovered_events

    # -- Lifecycle --------------------------------------------------------- #
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="jarvis-proactive-daemon")
        self._thread.start()
        log.info("⏰ Proactive Background Daemon started.")

    def _loop(self) -> None:
        interval = getattr(getattr(self.cfg, "daemon", None), "check_interval", 10.0)
        while not self._stop_event.wait(interval):
            try:
                self.tick()
            except Exception as exc:
                log.warn(f"Proactive daemon loop error: {exc}")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        log.info("⏰ Proactive Background Daemon stopped.")

    # -- Persistence ------------------------------------------------------- #
    def _load_rules(self) -> None:
        if not self.rules_path.exists():
            self._rules = self._default_rules()
            self._save_rules()
            return

        try:
            content = self.rules_path.read_text(encoding="utf-8")
            data = json.loads(content)
            self._rules = [EventRule.from_dict(d) for d in data if isinstance(d, dict)]
        except Exception as exc:
            log.warn(f"Could not load daemon rules from {self.rules_path}: {exc}")
            self._rules = self._default_rules()

    def _save_rules(self) -> None:
        try:
            self.rules_path.parent.mkdir(parents=True, exist_ok=True)
            self.rules_path.write_text(
                json.dumps([r.to_dict() for r in self._rules], indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            log.warn(f"Could not save daemon rules to {self.rules_path}: {exc}")
