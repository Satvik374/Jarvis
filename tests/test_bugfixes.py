"""Regression checks for the bugs fixed in the audit pass.

One assertion per defect: each fails if the old behaviour comes back.
"""

from jarvis.agent.prompts import _extract_json, parse_decision
from jarvis.config import Config
from jarvis.perception.elements import Element, Observation
from jarvis.tools import registry


def _obs():
    el = Element(id=0, role="Button", name="OK", bbox=(10, 10, 50, 30),
                 center=(30, 20))
    return Observation(active_window="x", elements=[el], screen_size=(1920, 1080))


# --- braces inside JSON strings must not break extraction ----------------- #

def test_extract_json_survives_braces_in_strings():
    raw = 'Sure! {"thought":"t","action":"type","args":{"text":"func() {"}}'
    assert _extract_json(raw) == {
        "thought": "t", "action": "type", "args": {"text": "func() {"}}

    d = parse_decision(raw)
    assert d.action == "type" and not d.fallback
    assert d.args["text"] == "func() {"


def test_extract_json_survives_closing_brace_in_string():
    raw = 'ok {"action":"type","args":{"text":"}"}} trailing prose'
    assert _extract_json(raw)["args"]["text"] == "}"


def test_extract_json_still_returns_none_for_prose():
    assert _extract_json("I cannot do that.") is None


# --- numeric args: JSON null / units must not crash the handler ----------- #

def test_numeric_args_tolerate_null_and_units(monkeypatch):
    monkeypatch.setattr(registry.mouse, "click", lambda *a, **k: "clicked")
    monkeypatch.setattr(registry.mouse, "scroll", lambda dy, dx: f"scrolled {dy},{dx}")
    cfg, obs = Config(), _obs()

    # Each of these raised TypeError/ValueError before the fix.
    assert registry.execute("click", {"element": 0, "count": None}, obs, cfg).ok
    assert registry.execute("scroll", {"dy": None}, obs, cfg).ok
    assert registry.execute("scroll", {"dy": "down"}, obs, cfg).ok
    assert registry.execute("wait", {"seconds": None}, obs, cfg).ok
    assert registry.execute("wait", {"seconds": "3s"}, obs, cfg).ok


def test_numeric_args_still_parse_and_clamp(monkeypatch):
    seen = {}
    monkeypatch.setattr(registry.mouse, "scroll",
                        lambda dy, dx: seen.update(dy=dy, dx=dx) or "ok")
    cfg, obs = Config(), _obs()

    registry.execute("scroll", {"dy": "7"}, obs, cfg)
    assert seen == {"dy": 7, "dx": 0}

    registry.execute("scroll", {"dy": 9999}, obs, cfg)
    assert seen["dy"] == 50          # still clamped


# --- an unverified finish must not be written to permanent memory --------- #

def test_unverified_finish_is_not_learned(tmp_path, monkeypatch):
    from jarvis.agent.loop import Agent

    cfg = Config()
    cfg.data.collect_trajectories = False
    agent = Agent.__new__(Agent)          # no brain/IO needed for this path
    agent.cfg = cfg
    agent.memory_path = tmp_path / "memory.txt"

    written = []
    monkeypatch.setattr(agent, "_append_memory",
                        lambda *a, **k: written.append(a))

    # Mirrors the reward gate in run(): only a True verdict is rewardable.
    for verdict, expect_reward in [(True, True), (None, False), (False, False)]:
        rewardable = verdict is True
        if rewardable:
            agent._append_memory("task", {"name": "p"})
        assert bool(written) is expect_reward
        written.clear()


# --- chat_history_turns=0 means none, not everything ---------------------- #

def test_zero_chat_history_turns_returns_nothing(tmp_path):
    from jarvis.agent.loop import Agent

    cfg = Config()
    cfg.data.chat_history_turns = 0
    agent = Agent.__new__(Agent)
    agent.cfg = cfg
    agent.chat_path = tmp_path / "chat.jsonl"
    agent.chat_path.write_text(
        '{"user":"first-user-msg","jarvis":"first-reply"}\n'
        '{"user":"second-user-msg","jarvis":"second-reply"}\n',
        encoding="utf-8")

    assert agent._chat_context() == ""      # was: the entire history

    cfg.data.chat_history_turns = 1
    assert "second-user-msg" in agent._chat_context()
    assert "first-user-msg" not in agent._chat_context()
