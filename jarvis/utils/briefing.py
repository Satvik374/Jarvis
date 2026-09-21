"""Human-friendly status: the startup briefing and the :status dashboard.

Two visible surfaces share one source of truth so they always agree:

- ``build_briefing``: the spoken greeting at startup. Instead of a generic
  "all systems online", Jarvis says when he last talked with you and what
  is scheduled, like a real assistant who remembers the previous day.
- ``format_status_report``: the ``:status`` dashboard. One command that
  answers "how are things?" across memory, schedules, and connections.

Every section must survive its data source being broken or missing:
failures degrade to a quiet note, never to a crash or a traceback.
"""

from __future__ import annotations

import datetime
import json
from typing import Any, Dict, List, Optional

from . import logging as log
from .logging import _c


# --------------------------------------------------------------------------- #
# gathering
# --------------------------------------------------------------------------- #

def _last_interaction(cfg: Any, agent: Any = None) -> Optional[datetime.datetime]:
    """When Jarvis last talked with the user, from the chat history file.

    The path lives on the live Agent (``agent.chat_path``); ``cfg`` is only
    a fallback so the helpers stay usable without one.
    """
    chat_path = getattr(agent, "chat_path", None) or getattr(cfg.data, "chat_path", None)
    if not chat_path:
        return None
    try:
        path = str(chat_path)
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            last = None
            for line in fh:  # last complete line wins
                line = line.strip()
                if not line:
                    continue
                try:
                    last = json.loads(line)
                except json.JSONDecodeError:
                    continue
        if last and last.get("ts"):
            return datetime.datetime.strptime(str(last["ts"])[:19], "%Y-%m-%d %H:%M:%S")
    except OSError:
        pass
    except Exception as exc:
        log.debug(f"briefing: chat history unread: {exc}")
    return None


def _memory_stats(agent: Any) -> Dict[str, Any]:
    """Facts learned and plans remembered; {} when memory is unavailable."""
    try:
        stats = agent.memory_mgr.get_stats()
        return {
            "facts": int(stats.get("facts_count", 0)),
            "plans": int(stats.get("learned_plans_count", 0)),
            "entities": int(stats.get("graph_entities", 0)),
            "relations": int(stats.get("graph_relations", 0)),
        }
    except Exception as exc:
        log.debug(f"briefing: memory stats unavailable: {exc}")
        return {}


def _cron_jobs() -> List[Dict[str, Any]]:
    """Scheduled jobs from the default scheduler, if one exists this session."""
    from .. import scheduler as _scheduler

    try:
        sched = _scheduler.get_default()
        if sched is None:
            return []
        return [
            {
                "command": str(getattr(job, "command", ""))[:60],
                "spec": str(getattr(job, "spec", "")),
            }
                for job in sched.jobs()
            ]
    except Exception as exc:
        log.debug(f"briefing: scheduler unavailable: {exc}")
        return []


def _proactive_rules() -> List[str]:
    """Names of enabled daemon rules; [] when the daemon is unavailable.

    Reads only a daemon that is ALREADY running this session: a greeting
    must never construct one (that would set the process singleton and
    spin up watchers as a side effect of saying hello).
    """
    try:
        from .. import daemon as _daemon_mod

        existing = getattr(_daemon_mod, "_DAEMON", None)
        if existing is None:
            return []
        rules = existing.list_rules() or []
        return [r.description for r in rules if getattr(r, "enabled", False)]
    except Exception as exc:
        log.debug(f"briefing: daemon unavailable: {exc}")
        return []


def _connector_lines() -> List[str]:
    """One human line per messenger connector; [] when unavailable."""
    try:
        from ..tools import connectors

        status = connectors.status()  # tolerant of dead connectors
    except Exception as exc:
        log.debug(f"briefing: connectors unavailable: {exc}")
        return []
    lines: List[str] = []
    if isinstance(status, dict):
        for name, info in status.items():
            try:
                unread = info.get("unread", 0) if isinstance(info, dict) else 0
                connected = bool(info.get("connected", False)) if isinstance(info, dict) else bool(info)
                label = str(name).replace("_", " ").title()
                if connected and unread:
                    lines.append(f"{unread} unread on {label}")
                elif connected:
                    lines.append(f"{label} connected")
            except Exception:
                continue
    return lines


def _friendly_delta(dt: datetime.datetime, now: datetime.datetime) -> str:
    """'just now' / '15 minutes ago' / '2 hours ago' / 'yesterday' / '4 days ago'."""
    delta = now - dt
    minutes = delta.total_seconds() / 60
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{int(minutes)} minutes ago" if int(minutes) != 1 else "a minute ago"
    hours = minutes / 60
    if hours < 24:
        return f"{int(hours)} hours ago" if int(hours) != 1 else "an hour ago"
    days = delta.days
    if days == 1:
        return "yesterday"
    return f"{days} days ago"


# --------------------------------------------------------------------------- #
# the two surfaces
# --------------------------------------------------------------------------- #

def build_briefing(cfg: Any, agent: Any = None) -> str:
    """The informative part of the spoken startup greeting.

    The console already says the time-aware hello; this adds what a real
    assistant would know: when you last talked and what is on the schedule.
    Returns ``""`` when there is nothing worth saying, so the caller can
    skip speaking entirely.
    """
    lines: List[str] = []

    last = _last_interaction(cfg, agent)
    if last:
        lines.append(f"We last spoke {_friendly_delta(last, datetime.datetime.now())}.")

    jobs = _cron_jobs()
    rules = _proactive_rules()
    upcoming: List[str] = [f"\"{j['command']}\" ({j['spec']})" for j in jobs[:2]]
    upcoming += [f"watching for {r}" for r in rules[:2]]
    if upcoming:
        lines.append("On the schedule: " + "; ".join(upcoming) + ".")

    mail = _connector_lines()
    if mail:
        lines.append("Mail: " + "; ".join(mail[:2]) + ".")

    return " ".join(lines).strip()


def format_status_report(cfg: Any, agent: Any = None) -> List[str]:
    """The ``:status`` dashboard, as printable console lines."""
    out: List[str] = []
    now = datetime.datetime.now()

    out.append(_c("JARVIS STATUS", "cyan"))
    out.append("")

    # --- Last interaction -------------------------------------------------
    last = _last_interaction(cfg, agent)
    if last:
        out.append(f"  {_c('Last chat', 'dim'):16}{last.strftime('%Y-%m-%d %H:%M')}  ({_friendly_delta(last, now)})")
    else:
        out.append(f"  {_c('Last chat', 'dim'):16}{_c('no chat history yet', 'grey')}")

    # --- Memory -----------------------------------------------------------
    mem = _memory_stats(agent) if agent is not None else {}
    if mem:
        parts = [f"{mem['facts']} facts", f"{mem['plans']} learned plans"]
        if mem.get("entities"):
            parts.append(f"{mem['entities']} graph entities")
        out.append(f"  {_c('Memory', 'dim'):16}{', '.join(parts)}")
    else:
        out.append(f"  {_c('Memory', 'dim'):16}{_c('unavailable this session', 'grey')}")

    # --- Scheduled jobs ---------------------------------------------------
    jobs = _cron_jobs()
    if jobs:
        out.append(f"  {_c('Scheduled', 'dim'):16}{len(jobs)} job(s):")
        for j in jobs[:5]:
            out.append(f"    {_c('•', 'cyan')} \"{j['command']}\"  {_c(j['spec'], 'grey')}")
    else:
        out.append(f"  {_c('Scheduled', 'dim'):16}{_c('nothing scheduled  (:cron add to schedule)', 'grey')}")

    # --- Proactive watchers ----------------------------------------------
    rules = _proactive_rules()
    if rules:
        out.append(f"  {_c('Watching for', 'dim'):16}{len(rules)} active watcher(s):")
        for r in rules[:5]:
            out.append(f"    {_c('•', 'cyan')}{r}")
    else:
        out.append(f"  {_c('Watching for', 'dim'):16}{_c('no proactive watchers  (:daemon status)', 'grey')}")

    # --- Connectors -------------------------------------------------------
    lines = _connector_lines()
    if lines:
        out.append(f"  {_c('Connections', 'dim'):16}{'; '.join(lines)}")
    else:
        out.append(f"  {_c('Connections', 'dim'):16}{_c('none available  (:connect)', 'grey')}")

    # --- Voice / vision / safety -----------------------------------------
    out.append("")
    voice_on = getattr(cfg, "voice_enabled", False)
    vision_on = getattr(getattr(cfg, "brain", None), "use_vision", False)
    steps = getattr(getattr(cfg, "safety", None), "max_steps", "?")
    out.append(f"  {_c('Voice', 'dim'):16}{_c('on', 'green') if voice_on else _c('off', 'grey')}"
               f"   {_c('Vision', 'dim')}{_c('on', 'green') if vision_on else _c('off', 'grey')}"
               f"   {_c('Max steps', 'dim')}{steps}")

    return out
