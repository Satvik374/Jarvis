"""Behavioral tests for the visible console surfaces.

Three features, one file: the informed startup briefing (utils.briefing),
the ``:status`` dashboard, and plain-English error messages
(``logging.friendly_error``). All hermetic: chat history, memory and the
scheduler are faked or pointed into a per-test sandbox - no real state.
"""

from __future__ import annotations

import datetime
import json
import types
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from jarvis.utils import briefing


def _cfg(chat_path=None):
    """Stand-in for Config with only what briefing reads."""
    return types.SimpleNamespace(data=types.SimpleNamespace(chat_path=chat_path))


def _write_chat(path: Path, ts: str, user: str = "open the report") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"ts": ts, "user": user, "jarvis": "done"}) + "\n",
                    encoding="utf-8")


def _agent_with_chat(path: Path):
    return types.SimpleNamespace(chat_path=path)


# --------------------------------------------------------------------------- #
# briefing: the spoken startup greeting
# --------------------------------------------------------------------------- #

def test_briefing_mentions_when_you_last_spoke(tmp_path):
    chat = tmp_path / "chat_memory.jsonl"
    yesterday = (datetime.datetime.now() - datetime.timedelta(days=1))
    _write_chat(chat, yesterday.strftime("%Y-%m-%d %H:%M:%S"), "fix the login page")

    text = briefing.build_briefing(_cfg(), _agent_with_chat(chat))
    assert "yesterday" in text
    assert "last spoke" in text.lower()


def test_briefing_is_empty_without_a_chat_history(tmp_path):
    text = briefing.build_briefing(_cfg(), _agent_with_chat(tmp_path / "missing.jsonl"))
    assert text == ""


def test_briefing_survives_broken_sources(tmp_path):
    chat = tmp_path / "chat_memory.jsonl"
    chat.write_text("this is not json\n", encoding="utf-8")

    with patch("jarvis.scheduler.get_default", side_effect=RuntimeError("boom")):
        text = briefing.build_briefing(_cfg(), _agent_with_chat(chat))
    assert isinstance(text, str)  # degraded, never raised


def test_briefing_never_starts_the_daemon(tmp_path):
    """The greeting reports on a running daemon; constructing one is a bug."""
    with patch("jarvis.daemon.get_daemon") as get_daemon:
        get_daemon.return_value.list_rules.return_value = []
        briefing.build_briefing(_cfg(), _agent_with_chat(tmp_path / "x.jsonl"))
    start = get_daemon.return_value.start
    start.assert_not_called()


# --------------------------------------------------------------------------- #
# :status dashboard
# --------------------------------------------------------------------------- #

def test_status_report_shows_every_section(tmp_path):
    chat = tmp_path / "chat_memory.jsonl"
    _write_chat(chat, "2026-09-20 18:55:51", "open github")
    agent = types.SimpleNamespace(
        chat_path=chat,
        memory_mgr=types.SimpleNamespace(get_stats=lambda: {
            "facts_count": 493, "learned_plans_count": 1,
            "graph_entities": 12, "graph_relations": 30,
            "total_vectors": 494, "db_path": "x", "memory_file": "y",
        }),
    )

    with patch("jarvis.scheduler.get_default", return_value=None):
        lines = briefing.format_status_report(_cfg(), agent=agent)

    joined = "\n".join(lines)
    assert "JARVIS STATUS" in joined
    assert "493 facts" in joined
    assert "1 learned plan" in joined
    assert "12 graph entities" in joined
    assert "2026-09-20 18:55" in joined
    assert "Voice" in joined and "Vision" in joined
    assert "Max steps" in joined


def test_status_report_without_agent_degrades(tmp_path):
    lines = briefing.format_status_report(_cfg(), agent=None)
    assert lines  # renders, no exception
    joined = "\n".join(lines)
    assert "unavailable this session" in joined


def test_status_report_plain_mode_is_bubble_safe():
    """The browser bubble collapses runs of spaces and must receive no ANSI
    codes - and singular/plural must read correctly at 1."""
    agent = types.SimpleNamespace(
        memory_mgr=types.SimpleNamespace(get_stats=lambda: {
            "facts_count": 493, "learned_plans_count": 1,
            "graph_entities": 12, "graph_relations": 30,
            "total_vectors": 494, "db_path": "x", "memory_file": "y",
        }),
    )
    lines = briefing.format_status_report(_cfg(), agent=agent, color=False)
    joined = "\n".join(lines)
    assert "\x1b[" not in joined
    assert "1 learned plan" in joined
    assert "1 learned plans" not in joined
    assert "Vision off" in joined  # labels never glued to values
    assert "Max steps" in joined
    assert " · " in joined  # dot separators survive space-collapsing


def test_status_report_shows_scheduled_jobs(tmp_path):
    job = types.SimpleNamespace(command="backup the folder", spec="daily at 09:00")
    sched = types.SimpleNamespace(jobs=lambda: [job])

    with patch("jarvis.scheduler.get_default", return_value=sched):
        lines = briefing.format_status_report(_cfg(), agent=None)

    joined = "\n".join(lines)
    assert "1 job(s)" in joined
    assert "backup the folder" in joined


# --------------------------------------------------------------------------- #
# plain-English errors
# --------------------------------------------------------------------------- #

from jarvis.utils.logging import friendly_error  # noqa: E402


def test_connection_error_is_plain():
    out = friendly_error(ConnectionError("[Errno 10061] refused"))
    assert "couldn't reach the service" in out
    assert "Errno" not in out


def test_timeout_error_is_plain():
    out = friendly_error(TimeoutError("operation timed out"))
    assert "took too long" in out


def test_permission_error_is_plain():
    out = friendly_error(PermissionError("[WinError 5] Access is denied"))
    assert "permission" in out
    assert "administrator" in out


def test_missing_module_names_the_piece():
    out = friendly_error(ModuleNotFoundError("No module named 'pyaudio'"))
    assert "pyaudio" in out
    assert "pip install" in out


def test_rate_limit_is_plain():
    out = friendly_error(RuntimeError("Error 429: rate limit exceeded"))
    assert "slow down" in out or "busy" in out


def test_unknown_error_names_the_kind_and_points_to_the_log():
    out = friendly_error(RuntimeError("exotic failure 0xBEEF"))
    assert "Something went wrong" in out
    assert "RuntimeError" in out


def test_friendly_errors_are_never_lorem_technical():
    """Whatever comes in, the reply must not end in a traceback fragment."""
    for exc in (ValueError("x"), KeyError("y"), OSError("nope")):
        out = friendly_error(exc)
        assert out and out.endswith(".")


# --------------------------------------------------------------------------- #
# end-to-end: the real repl dispatches :status to the dashboard
# --------------------------------------------------------------------------- #

def test_status_command_driven_through_the_real_repl(monkeypatch, tmp_path, capsys):
    """Drive console.repl() itself with scripted input and prove the real
    ``:status`` branch prints the dashboard. Heavy externals (brain, agent,
    TTS, scheduler thread, daemon) are stubbed; the dispatch logic under
    test is the real one."""
    from jarvis import console, scheduler as scheduler_mod, daemon as daemon_mod
    from jarvis.config import load_config

    inputs = iter([":status", ":quit"])
    monkeypatch.setattr(console, "_read_input", lambda prompt: next(inputs))
    monkeypatch.setattr(console, "make_brain", lambda cfg_brain: Mock(name="brain"))
    monkeypatch.setattr(console, "Agent", lambda brain, cfg: Mock(name="agent"))
    monkeypatch.setattr(console.voice, "speak", lambda *a, **k: None)
    monkeypatch.setattr(console, "cron_store_path", lambda: tmp_path / "cron.json")
    monkeypatch.setattr(scheduler_mod, "Scheduler",
                        lambda *a, **k: Mock(name="scheduler"))
    monkeypatch.setattr(daemon_mod, "start_daemon", lambda **k: Mock(name="daemon"))

    rc = console.repl(load_config())

    assert rc == 0
    out = capsys.readouterr().out
    assert "JARVIS STATUS" in out
    assert "Last chat" in out
    assert "Memory" in out
    assert "Scheduled" in out
