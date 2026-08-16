"""Event models and rule definitions for the Proactive Background Daemon."""

from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional


class EventType(enum.Enum):
    """Supported trigger event categories."""
    BATTERY_LOW = "battery_low"
    BATTERY_CHARGING = "battery_charging"
    BATTERY_DISCHARGING = "battery_discharging"
    HIGH_CPU = "high_cpu"
    HIGH_MEMORY = "high_memory"
    FILE_DROPPED = "file_dropped"
    APP_LAUNCHED = "app_launched"
    MORNING_ROUTINE = "morning_routine"
    EVENING_ROUTINE = "evening_routine"
    CLIPBOARD_SUGGESTION = "clipboard_suggestion"
    CUSTOM = "custom"


@dataclass
class Event:
    """A detected hardware, system, or temporal event."""
    type: EventType
    title: str
    message: str
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    source: str = "system"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "title": self.title,
            "message": self.message,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "source": self.source,
        }


@dataclass
class EventRule:
    """A proactive automation rule: when trigger occurs, evaluate condition and execute action."""
    id: str = field(default_factory=lambda: f"rule_{str(uuid.uuid4())[:8]}")
    name: str = "Unnamed Rule"
    trigger_type: EventType = EventType.CUSTOM
    condition: Dict[str, Any] = field(default_factory=dict)
    action_type: str = "notify"  # "notify" (TTS/UI announcement), "task" (agent.run), "macro" (replay macro)
    action_target: str = ""      # text prompt, macro name, or message template
    cooldown_seconds: float = 300.0  # default 5 minute cooldown between repeated triggers
    last_triggered: float = 0.0
    enabled: bool = True

    def matches(self, event: Event, now: float | None = None) -> bool:
        """Evaluate whether this rule should fire on the given event."""
        if not self.enabled:
            return False

        if event.type != self.trigger_type:
            return False

        current_time = now if now is not None else time.time()
        if (current_time - self.last_triggered) < self.cooldown_seconds:
            return False

        # Condition checks (if specified)
        if self.condition:
            for k, expected in self.condition.items():
                actual = event.payload.get(k)
                if actual is None:
                    continue

                # Threshold numerical checks (e.g. min_threshold or max_threshold)
                if k.endswith("_lt") or k == "below":
                    if not (isinstance(actual, (int, float)) and actual < expected):
                        return False
                elif k.endswith("_gt") or k == "above":
                    if not (isinstance(actual, (int, float)) and actual > expected):
                        return False
                elif isinstance(expected, str) and isinstance(actual, str):
                    if expected.lower() not in actual.lower():
                        return False
                elif actual != expected:
                    return False

        return True

    def mark_triggered(self, now: float | None = None) -> None:
        self.last_triggered = now if now is not None else time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "trigger_type": self.trigger_type.value,
            "condition": self.condition,
            "action_type": self.action_type,
            "action_target": self.action_target,
            "cooldown_seconds": self.cooldown_seconds,
            "last_triggered": self.last_triggered,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EventRule:
        data_copy = dict(data)
        trigger_raw = data_copy.pop("trigger_type", "custom")
        try:
            trigger_type = EventType(trigger_raw)
        except ValueError:
            trigger_type = EventType.CUSTOM
        return cls(trigger_type=trigger_type, **data_copy)
