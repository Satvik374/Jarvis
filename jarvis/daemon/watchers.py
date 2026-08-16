"""Proactive background event watchers for hardware, OS resources, files, and routines."""

from __future__ import annotations

import ctypes
from datetime import datetime
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Set

from .events import Event, EventType


class BaseWatcher:
    """Base interface for background state observers."""
    def __init__(self, name: str = "watcher"):
        self.name = name
        self.enabled = True

    def check(self, now: float | None = None) -> List[Event]:
        """Perform a single state check and return any triggered events."""
        raise NotImplementedError


class BatteryWatcher(BaseWatcher):
    """Monitors battery percentage, charging state transitions, and low battery thresholds."""

    def __init__(self, low_threshold: int = 20):
        super().__init__(name="battery_watcher")
        self.low_threshold = low_threshold
        self.last_plugged: Optional[bool] = None
        self.last_percent: Optional[int] = None
        self.has_alerted_low = False

    def check(self, now: float | None = None) -> List[Event]:
        if not self.enabled:
            return []

        events: List[Event] = []
        try:
            import psutil
            battery = psutil.sensors_battery()
            if battery is None:
                return []

            percent = int(battery.percent)
            plugged = bool(battery.power_plugged)

            # 1. State change: Plugged in / Unplugged
            if self.last_plugged is not None and plugged != self.last_plugged:
                if plugged:
                    events.append(Event(
                        type=EventType.BATTERY_CHARGING,
                        title="Power Connected",
                        message=f"AC power connected. Battery is at {percent}%.",
                        payload={"percent": percent, "plugged": True},
                        source="battery",
                    ))
                    self.has_alerted_low = False
                else:
                    events.append(Event(
                        type=EventType.BATTERY_DISCHARGING,
                        title="On Battery Power",
                        message=f"AC power disconnected. Running on battery ({percent}% remaining).",
                        payload={"percent": percent, "plugged": False},
                        source="battery",
                    ))

            # 2. Low battery trigger
            if not plugged and percent <= self.low_threshold and not self.has_alerted_low:
                events.append(Event(
                    type=EventType.BATTERY_LOW,
                    title="Low Battery Warning",
                    message=f"Battery level is low ({percent}%). Please plug in your charger.",
                    payload={"percent": percent, "plugged": False, "below": self.low_threshold},
                    source="battery",
                ))
                self.has_alerted_low = True

            # Reset low alert flag if battery went back above threshold
            if percent > (self.low_threshold + 5):
                self.has_alerted_low = False

            self.last_plugged = plugged
            self.last_percent = percent

        except Exception:
            pass

        return events


class ResourceWatcher(BaseWatcher):
    """Monitors CPU and RAM consumption against threshold levels."""

    def __init__(self, cpu_threshold: int = 90, memory_threshold: int = 85):
        super().__init__(name="resource_watcher")
        self.cpu_threshold = cpu_threshold
        self.memory_threshold = memory_threshold
        self.high_cpu_streak = 0
        self.high_mem_streak = 0

    def check(self, now: float | None = None) -> List[Event]:
        if not self.enabled:
            return []

        events: List[Event] = []
        try:
            import psutil
            cpu_val = psutil.cpu_percent(interval=None)
            mem_val = psutil.virtual_memory().percent

            # Check CPU spike (require 2 consecutive ticks to avoid momentary blips)
            if cpu_val >= self.cpu_threshold:
                self.high_cpu_streak += 1
                if self.high_cpu_streak >= 2:
                    events.append(Event(
                        type=EventType.HIGH_CPU,
                        title="High CPU Utilization",
                        message=f"Sustained high CPU usage detected ({cpu_val:.1f}%).",
                        payload={"cpu_percent": cpu_val, "above": self.cpu_threshold},
                        source="system_resources",
                    ))
                    self.high_cpu_streak = 0
            else:
                self.high_cpu_streak = 0

            # Check Memory spike
            if mem_val >= self.memory_threshold:
                self.high_mem_streak += 1
                if self.high_mem_streak >= 2:
                    events.append(Event(
                        type=EventType.HIGH_MEMORY,
                        title="High Memory Usage",
                        message=f"System RAM consumption is high ({mem_val:.1f}%).",
                        payload={"memory_percent": mem_val, "above": self.memory_threshold},
                        source="system_resources",
                    ))
                    self.high_mem_streak = 0
            else:
                self.high_mem_streak = 0

        except Exception:
            pass

        return events


class FileWatcher(BaseWatcher):
    """Monitors directories (e.g. ~/Downloads) for newly dropped files."""

    def __init__(self, directories: tuple[str, ...] | list[str] = ("~/Downloads",)):
        super().__init__(name="file_watcher")
        self.paths = [Path(os.path.expanduser(p)).resolve() for p in directories]
        self.known_files: Dict[Path, Set[str]] = {}
        self._initialized = False

    def check(self, now: float | None = None) -> List[Event]:
        if not self.enabled:
            return []

        events: List[Event] = []
        for path in self.paths:
            if not path.is_dir():
                continue

            try:
                current_files = {
                    f.name for f in path.iterdir()
                    if f.is_file() and not f.name.endswith(".tmp") and not f.name.endswith(".crdownload")
                }
            except Exception:
                continue

            if not self._initialized:
                self.known_files[path] = current_files
                continue

            previous_files = self.known_files.get(path, set())
            new_files = current_files - previous_files

            for fname in new_files:
                fpath = path / fname
                events.append(Event(
                    type=EventType.FILE_DROPPED,
                    title="New File Received",
                    message=f"New file dropped in {path.name}: {fname}",
                    payload={"file_name": fname, "path": str(fpath), "directory": str(path)},
                    source="file_watcher",
                ))

            self.known_files[path] = current_files

        self._initialized = True
        return events


class WindowWatcher(BaseWatcher):
    """Monitors active foreground application transitions."""

    def __init__(self):
        super().__init__(name="window_watcher")
        self.last_window_title = ""

    def check(self, now: float | None = None) -> List[Event]:
        if not self.enabled:
            return []

        events: List[Event] = []
        try:
            GetForegroundWindow = ctypes.windll.user32.GetForegroundWindow
            GetWindowTextLength = ctypes.windll.user32.GetWindowTextLengthW
            GetWindowText = ctypes.windll.user32.GetWindowTextW

            hwnd = GetForegroundWindow()
            if hwnd:
                length = GetWindowTextLength(hwnd)
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    GetWindowText(hwnd, buff, length + 1)
                    current_title = buff.value.strip()

                    if current_title and current_title != self.last_window_title:
                        if self.last_window_title and not any(t in current_title.lower() for t in ("taskbar", "program manager", "desktop")):
                            events.append(Event(
                                type=EventType.APP_LAUNCHED,
                                title="Application Switched",
                                message=f"Active application changed to '{current_title}'.",
                                payload={"app_title": current_title, "previous_title": self.last_window_title},
                                source="window_watcher",
                            ))
                        self.last_window_title = current_title
        except Exception:
            pass

        return events


class RoutineWatcher(BaseWatcher):
    """Triggers time-of-day proactive daily routines (Morning Briefing & Evening Summary)."""

    def __init__(
        self,
        morning_time: str = "09:00",
        morning_enabled: bool = True,
        evening_time: str = "21:00",
        evening_enabled: bool = True,
    ):
        super().__init__(name="routine_watcher")
        self.morning_time = morning_time
        self.morning_enabled = morning_enabled
        self.evening_time = evening_time
        self.evening_enabled = evening_enabled
        self.last_morning_date: Optional[str] = None
        self.last_evening_date: Optional[str] = None

    def check(self, now: float | None = None) -> List[Event]:
        if not self.enabled:
            return []

        events: List[Event] = []
        dt = datetime.fromtimestamp(now) if now is not None else datetime.now()
        today_str = dt.strftime("%Y-%m-%d")
        current_hm = dt.strftime("%H:%M")

        # Morning Briefing check
        if self.morning_enabled and current_hm >= self.morning_time and self.last_morning_date != today_str:
            events.append(Event(
                type=EventType.MORNING_ROUTINE,
                title="Morning Briefing",
                message="Good morning, sir. System is online. Ready for your morning briefing.",
                payload={"time": current_hm, "date": today_str, "routine": "morning"},
                source="routine_watcher",
            ))
            self.last_morning_date = today_str

        # Evening Summary check
        if self.evening_enabled and current_hm >= self.evening_time and self.last_evening_date != today_str:
            events.append(Event(
                type=EventType.EVENING_ROUTINE,
                title="Evening Summary",
                message="Good evening, sir. Ready to compile your daily workspace summary.",
                payload={"time": current_hm, "date": today_str, "routine": "evening"},
                source="routine_watcher",
            ))
            self.last_evening_date = today_str

        return events


class ClipboardWatcher(BaseWatcher):
    """Monitors system clipboard changes for URLs, code snippets, error traces, and actionable patterns."""

    def __init__(self):
        super().__init__(name="clipboard_watcher")
        self.last_clip: str = ""
        self._initialized = False

    def check(self, now: float | None = None) -> List[Event]:
        if not self.enabled:
            return []

        events: List[Event] = []
        try:
            import pyperclip  # type: ignore
            text = pyperclip.paste()
        except Exception:
            return []

        if not text or not isinstance(text, str):
            return []

        clean = text.strip()
        if not clean or clean == self.last_clip:
            return []

        if not self._initialized:
            self.last_clip = clean
            self._initialized = True
            return []

        self.last_clip = clean

        # 1. URL pattern
        if clean.startswith("http://") or clean.startswith("https://"):
            events.append(Event(
                type=EventType.CLIPBOARD_SUGGESTION,
                title="URL Copied",
                message=f"URL copied to clipboard: {clean[:80]}",
                payload={"url": clean, "kind": "url"},
                source="clipboard_watcher",
            ))
        # 2. Error / Traceback pattern
        elif "traceback (most recent call last)" in clean.lower() or "syntaxerror:" in clean.lower() or "error:" in clean.lower():
            events.append(Event(
                type=EventType.CLIPBOARD_SUGGESTION,
                title="Error Traceback Detected",
                message="Code error detected in clipboard. Ready to debug.",
                payload={"snippet": clean[:500], "kind": "error"},
                source="clipboard_watcher",
            ))

        return events

