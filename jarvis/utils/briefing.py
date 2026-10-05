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

from ..agent.brain import VisionState
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
    """Facts learned and graph size; {} when memory is unavailable."""
    try:
        stats = agent.memory_mgr.get_stats()
        return {
            "facts": int(stats.get("facts_count", 0)),
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
    """One human line per messenger connector; [] when unavailable.

    Asks for the machine-readable state, not the wording ``status()`` returns:
    the old version was written for a dict and handed that string, so this line
    could never render anything. It says "ready" rather than "connected"
    because all that is known here is that the credentials are present.
    """
    try:
        from ..tools import connectors

        states = connectors.states()  # tolerant of dead connectors
    except Exception as exc:
        log.debug(f"briefing: connectors unavailable: {exc}")
        return []
    try:
        return [f"{name} ready" for name, info in states.items()
                if info.get("configured")]
    except Exception:
        return []


def _away_report() -> str:
    """What WhatsApp callers left word about; "" when there is nothing to say.

    Reads and consumes the away assistant's report - the startup briefing is the
    "you are back" moment, so it is the right place to say it, once. Read here
    rather than pushed in, so a broken assistant degrades to a quiet note.
    """
    try:
        from ..whatsapp_away import report

        return report(mark=True)
    except Exception as exc:
        log.debug(f"briefing: away report unavailable: {exc}")
        return ""


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

    away = _away_report()
    if away:
        lines.append(away)

    return " ".join(lines).strip()


def _vision_view(cfg: Any, agent: Any) -> VisionState:
    """The live answer, from its owner, with the setting as a fallback.

    The brain is the authority - it is what pauses screenshots after a refusal -
    so reporting ``cfg.brain.use_vision`` here would print a setting as though it
    were a measurement. The fallback only covers the no-agent case.
    """
    brain = getattr(agent, "brain", None) if agent is not None else None
    if brain is not None and hasattr(brain, "vision_state"):
        return brain.vision_state()
    on = bool(getattr(getattr(cfg, "brain", None), "use_vision", False))
    return VisionState(on, on)


def _vision_label(view: VisionState) -> str:
    """Plain words for on / off / paused-for-a-bit, for a two-word field."""
    if view.usable:
        return "on"
    if not view.configured:
        return "off"
    wait = (f"~{max(1, round(view.retry_in / 60))} min" if view.retry_in >= 60
            else f"{view.retry_in}s")
    return f"paused {wait} ({view.reason})" if view.reason else f"paused {wait}"


def format_status_report(cfg: Any, agent: Any = None, color: bool = True) -> List[str]:
    """The ``:status`` dashboard, as printable console lines.

    ``color=False`` returns plain text (no ANSI codes) for surfaces that
    render text themselves, like the browser chat bubble.
    """
    dim = (lambda s: _c(s, "dim")) if color else (lambda s: s)
    grey = (lambda s: _c(s, "grey")) if color else (lambda s: s)
    cyan = (lambda s: _c(s, "cyan")) if color else (lambda s: s)
    green = (lambda s: _c(s, "green")) if color else (lambda s: s)
    yellow = (lambda s: _c(s, "yellow")) if color else (lambda s: s)
    out: List[str] = []
    now = datetime.datetime.now()

    out.append(cyan("JARVIS STATUS"))
    out.append("")

    # --- Last interaction -------------------------------------------------
    last = _last_interaction(cfg, agent)
    if last:
        out.append(f"  {dim('Last chat'):16}{last.strftime('%Y-%m-%d %H:%M')}  ({_friendly_delta(last, now)})")
    else:
        out.append(f"  {dim('Last chat'):16}{grey('no chat history yet')}")

    # --- Memory -----------------------------------------------------------
    mem = _memory_stats(agent) if agent is not None else {}
    if mem:
        parts = [
            f"{mem['facts']} fact{'' if mem['facts'] == 1 else 's'}",
        ]
        if mem.get("entities"):
            parts.append(f"{mem['entities']} graph entities")
        out.append(f"  {dim('Memory'):16}{', '.join(parts)}")
    else:
        out.append(f"  {dim('Memory'):16}{grey('unavailable this session')}")

    # --- Scheduled jobs ---------------------------------------------------
    jobs = _cron_jobs()
    if jobs:
        out.append(f"  {dim('Scheduled'):16}{len(jobs)} job(s):")
        for j in jobs[:5]:
            out.append(f"    {cyan('•')} \"{j['command']}\"  {grey(j['spec'])}")
    else:
        out.append(f"  {dim('Scheduled'):16}{grey('nothing scheduled  (:cron add to schedule)')}")

    # --- Proactive watchers ----------------------------------------------
    rules = _proactive_rules()
    if rules:
        out.append(f"  {dim('Watching for'):16}{len(rules)} active watcher(s):")
        for r in rules[:5]:
            out.append(f"    {cyan('•')}{r}")
    else:
        out.append(f"  {dim('Watching for'):16}{grey('no proactive watchers  (:daemon status)')}")

    # --- Connectors -------------------------------------------------------
    lines = _connector_lines()
    if lines:
        out.append(f"  {dim('Connections'):16}{'; '.join(lines)}")
    else:
        out.append(f"  {dim('Connections'):16}{grey('none available  (:connect)')}")

    # --- While you were away ----------------------------------------------
    try:
        from ..whatsapp_away import report as _away, session_report as _session

        # What this session has already told you (the greeting, and any live
        # notice) plus anything not consumed yet. Read both so the dashboard shows
        # the same caller facts the greeting showed instead of an empty line,
        # which is all that would be left once the greeting had consumed them.
        away = " ".join(p for p in (_session(), _away(mark=False)) if p)
    except Exception as exc:
        log.debug(f"status: away report unavailable: {exc}")
        away = ""
    if away:
        out.append(f"  {dim('While away'):16}{away}")

    # --- Voice / vision / safety -----------------------------------------
    out.append("")
    voice_on = getattr(cfg, "voice_enabled", False)
    vision = _vision_view(cfg, agent)
    steps = getattr(getattr(cfg, "safety", None), "max_steps", "?")
    voice_state = "on" if voice_on else "off"
    vision_state = _vision_label(vision)
    vision_paint = green if vision.usable else (grey if not vision.configured else yellow)
    if color:
        out.append(f"  {dim('Voice'):16}{green(voice_state) if voice_on else grey(voice_state)}"
                   f"   {dim('Vision'):12}{vision_paint(vision_state)}"
                   f"   {dim('Max steps'):12}{steps}")
    else:
        # Plain-text surfaces collapse runs of spaces, so separate with dots.
        out.append(f"Voice {voice_state} · Vision {vision_state} · Max steps {steps}")

    return out
