"""Proactive Background Daemon & Event-Driven Automation Package."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from ..config import Config
from .engine import ProactiveDaemon
from .events import Event, EventRule, EventType

_DAEMON: Optional[ProactiveDaemon] = None


def get_daemon(
    cfg: Optional[Config] = None,
    task_runner: Optional[Callable[[str], None]] = None,
    rules_path: Optional[Path | str] = None,
) -> ProactiveDaemon:
    """Retrieve or initialize the active singleton ProactiveDaemon instance."""
    global _DAEMON
    if _DAEMON is None:
        _DAEMON = ProactiveDaemon(cfg=cfg, task_runner=task_runner, rules_path=rules_path)
    return _DAEMON


def set_daemon(daemon: Optional[ProactiveDaemon]) -> None:
    global _DAEMON
    _DAEMON = daemon


def start_daemon(
    cfg: Optional[Config] = None,
    task_runner: Optional[Callable[[str], None]] = None,
) -> ProactiveDaemon:
    daemon = get_daemon(cfg=cfg, task_runner=task_runner)
    daemon.start()
    return daemon


def stop_daemon() -> None:
    global _DAEMON
    if _DAEMON is not None:
        _DAEMON.stop()


__all__ = [
    "ProactiveDaemon",
    "Event",
    "EventRule",
    "EventType",
    "get_daemon",
    "set_daemon",
    "start_daemon",
    "stop_daemon",
]
