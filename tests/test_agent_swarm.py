import pytest
import time
from unittest.mock import MagicMock

from jarvis.config import Config
from jarvis.agent import subagent
from jarvis.agent.brain import Brain
from jarvis.tools import registry
from jarvis.perception.elements import Observation


class DummyBrain(Brain):
    def __init__(self, reply_map=None):
        self.reply_map = reply_map or {}
        self.call_count = 0

    def complete(self, system: str, messages: list[dict], image=None) -> str:
        self.call_count += 1
        last_msg = messages[-1]["content"] if messages else ""
        for k, v in self.reply_map.items():
            if k in last_msg or k in system:
                return v
        return '{"thought": "done", "action": "finish", "args": {"message": "Success"}}'


def test_specialist_subagents_registered():
    specs = subagent.available()
    assert "researcher" in specs
    assert "verifier" in specs
    assert "data_analyst" in specs
    assert "architect" in specs

    assert "pass / fail" in specs["verifier"].prompt.lower()
    assert "insights" in specs["data_analyst"].prompt.lower()
    assert "architect" in specs["architect"].prompt.lower()


def test_subagent_run_swarm_concurrency(monkeypatch):
    executed_names = []

    def mock_run_agent(spec, brain, cfg, task):
        time.sleep(0.05)
        executed_names.append(spec.name)
        return f"Report from {spec.name} for task: {task}", False

    monkeypatch.setattr(subagent, "run_agent", mock_run_agent)

    tasks = [
        {"name": "researcher", "task": "Research topic A"},
        {"name": "verifier", "task": "Verify build B"},
        {"name": "data_analyst", "task": "Analyze metrics C"},
    ]

    cfg = Config()
    brain = DummyBrain()

    t0 = time.time()
    report, is_ask = subagent.run_swarm(tasks, brain, cfg, max_workers=3)
    duration = time.time() - t0

    # 3 concurrent tasks taking 0.05s each should complete in ~0.08s (much less than 3 * 0.05 = 0.15s)
    assert duration < 0.35
    assert len(executed_names) == 3
    assert "RESEARCHER" in report
    assert "VERIFIER" in report
    assert "DATA_ANALYST" in report
    assert is_ask is False


def test_agent_swarm_tool_registry(monkeypatch):
    monkeypatch.setattr(
        subagent,
        "run_swarm",
        lambda tasks, brain, cfg, max_workers=4, timeout=180.0: ("Mock Swarm Result", False)
    )

    cfg = Config()
    obs = Observation(elements=[], screen_size=(1920, 1080))
    args = {
        "tasks": [
            {"name": "researcher", "task": "Check docs"},
            {"name": "verifier", "task": "Run tests"},
        ],
        "timeout": 60,
    }

    res = registry.execute("agent_swarm", args, obs, cfg)
    assert res.ok is True
    assert "Mock Swarm Result" in res.message


def test_agent_swarm_empty_tasks():
    cfg = Config()
    brain = DummyBrain()
    report, is_ask = subagent.run_swarm([], brain, cfg)
    assert "No swarm tasks" in report
    assert is_ask is False
