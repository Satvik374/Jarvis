"""Avoid wasted perception work without caching screens or skipping verification.

The synthetic UIA benchmark is also runnable without a desktop or a provider:
    python -m tests.test_task_speed
It measures Python traversal and property-call counts, NOT real COM/API latency.
"""
from __future__ import annotations

from collections import Counter
from contextlib import ExitStack
import json
import statistics
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

from jarvis.agent import loop
from jarvis.config import Config
from jarvis.perception import _comtypes_fix, elements
from jarvis.perception.elements import Element, Observation
from jarvis.tools import registry


class Control:
    def __init__(self, calls, role, name="", rect=(0, 0, 100, 100), children=()):
        self.calls = calls
        self.ControlTypeName = role + "Control"
        self.name = name
        self.BoundingRectangle = SimpleNamespace(
            left=rect[0], top=rect[1], right=rect[2], bottom=rect[3])
        self.children = list(children)

    @property
    def Name(self):
        self.calls["names"] += 1
        return self.name

    def GetChildren(self):
        self.calls["children"] += 1
        return self.children


def tree(calls):
    # Offscreen controls and surplus browser chrome still cost COM Name reads
    # unless eligibility is checked first. A rejected chrome node can contain
    # the actual page, so its children must STILL be traversed.
    offscreen = [Control(calls, "Button", f"hidden-{i}",
                         rect=(0, 1500 + i * 10, 100, 1508 + i * 10))
                 for i in range(180)]
    chrome = [Control(calls, "Button", f"chrome-{i}",
                      rect=(10 + i * 10, 10, 18 + i * 10, 30))
              for i in range(100)]
    page = Control(calls, "Document", "Test page", (0, 100, 1200, 900), [
        Control(calls, "Edit", "", (20, 200, 200, 240)),
        Control(calls, "Button", "Submit", (20, 260, 100, 300)),
    ])
    chrome[-1].children = [page]
    return Control(calls, "Window", children=offscreen + chrome)


def walk(root, max_elements=60):
    auto = SimpleNamespace(GetForegroundControl=lambda: root,
                           Logger=Mock())
    with (
        patch.dict(sys.modules, {"uiautomation": auto}),
        patch.object(_comtypes_fix, "ensure"),
        patch("jarvis.desktop.is_shadow_enabled", return_value=False),
    ):
        return elements._detect_uia(max_elements, (1920, 1080),
                                    window_title="Test page - Browser")


def test_uia_reads_names_only_for_eligible_controls_and_documents():
    calls = Counter()
    found = walk(tree(calls))
    assert [(e.role, e.name) for e in found] == (
        [("Button", f"chrome-{i}") for i in range(18)]
        + [("Document", "Test page"), ("Edit", ""), ("Button", "Submit")])
    assert calls["names"] == 21
    assert found[-1].id == 20
    assert found[-1].center == (60, 280)


def test_uia_does_not_expand_the_last_element_after_budget_is_full():
    calls = Counter()
    root = Control(calls, "Button", "Target")
    assert walk(root, max_elements=1)[0].name == "Target"
    assert calls["children"] == 0


def test_document_name_is_read_even_when_its_box_cannot_be_kept():
    calls = Counter()
    # Full-width documents are not clickable, but their title is essential
    # for detecting stale browser trees. Its child is still a valid target.
    root = Control(calls, "Document", "Test page", (0, 0, 1920, 1080), [
        Control(calls, "Button", "Submit", (20, 260, 100, 300))])
    assert [e.name for e in walk(root)] == ["Submit"]
    assert calls["names"] == 2


def observation(name="Ready"):
    return Observation([Element(0, "Button", name, (10, 10, 40, 30), (25, 20))],
                       (1920, 1080), "Test window")


def run_task(decisions, execute, *, yolo=False):
    """Drive the real loop with no user state, network or desktop access."""
    agent = object.__new__(loop.Agent)
    agent.cfg = Config()
    agent.cfg.safety.self_healing = False
    agent.cfg.safety.max_steps = 10
    agent.cfg.perception.save_screenshots = False
    agent.cancel_event = threading.Event()
    agent.writer = Mock()
    agent.brain = Mock()
    agent.brain.vision_state.return_value = SimpleNamespace(usable=True)
    agent.brain.complete.side_effect = [json.dumps(d) for d in decisions]
    agent._read_memory = Mock(return_value="")
    agent._chat_context = Mock(return_value="")
    agent._append_chat = Mock()
    agent._perceive = Mock(side_effect=lambda: observation("Initial"))
    agent._maybe_image = Mock(side_effect=lambda *args: object())
    agent._verify_success = Mock(return_value=(True, "verified"))
    agent._confirm = Mock(return_value=True)
    external_image = object()

    with ExitStack() as stack:
        for name in ("jarvis.mcp.tools_note", "jarvis.remote.note",
                     "jarvis.skills.note", "jarvis.memory.coordinates.note",
                     "jarvis.tools.connectors.note",
                     "jarvis.tools.tool_synthesis.synthesized_tools_prompt_note"):
            stack.enter_context(patch(name, return_value=""))
        stack.enter_context(patch.object(loop, "agents_note", return_value=""))
        stack.enter_context(patch.object(loop, "_find_image", side_effect=(
            lambda task: external_image if task == '"external.png"' else None)))
        stack.enter_context(patch.object(loop.log, "pop"))
        stack.enter_context(patch.object(registry, "execute", side_effect=execute))
        result = agent.run("open the test page" + (" -yolo" if yolo else ""))
    assert result == "Done"
    assert agent.writer.save.call_count == 1
    return agent, external_image


FINISH = {"action": "finish", "args": {"summary": "Done"}}


def finished():
    return registry.ActionResult(True, "Done", needs_observe=False, finished=True)


def test_browser_or_remote_frame_skips_unused_desktop_capture_and_is_verified():
    decisions = [{"action": "browser_action", "args": {"action": "snapshot"}},
                 {"action": "read_file", "args": {"path": "test.txt"}}, FINISH]
    results = iter([
        registry.ActionResult(True, "page", needs_observe=False,
                              image_path="external.png"),
        registry.ActionResult(True, "text", needs_observe=False), finished()])
    agent, image = run_task(decisions, lambda *args: next(results))
    assert agent._maybe_image.call_count == 1
    assert [call.kwargs["image"] for call in agent.brain.complete.call_args_list][1:] == [image, image]
    assert agent._verify_success.call_args.kwargs["image"] is image
    assert agent._verify_success.call_count == 1


def test_clearing_external_image_restores_fresh_desktop_capture():
    decisions = [{"action": "browser_action", "args": {"action": "snapshot"}},
                 {"action": "browser_action", "args": {"action": "close"}}, FINISH]
    results = iter([
        registry.ActionResult(True, "page", needs_observe=False,
                              image_path="external.png"),
        registry.ActionResult(True, "closed", needs_observe=False, clear_image=True),
        finished()])
    agent, image = run_task(decisions, lambda *args: next(results))
    assert agent._maybe_image.call_count == 2
    images = [call.kwargs["image"] for call in agent.brain.complete.call_args_list]
    assert images[1] is image
    assert images[2] is not image
    assert images[2] is not images[0], "never reuse an old desktop frame"
    assert agent._verify_success.call_args.kwargs["image"] is images[2]


def test_wait_for_returns_the_observation_that_found_the_element():
    obs = observation()
    with (patch.object(registry.apps, "list_windows", return_value=[]),
          patch.object(elements, "observe", return_value=obs)):
        result = registry._h_wait_for({"target": "ready"}, None, Config())
    assert result.ok
    assert result.needs_observe
    assert getattr(result, "observation", None) is obs


def test_wait_for_window_match_still_requires_a_fresh_observation():
    with (patch.object(registry.apps, "list_windows", return_value=["Ready"]),
          patch.object(elements, "observe") as observe):
        result = registry._h_wait_for({"target": "ready"}, None, Config())
    assert result.ok
    assert result.needs_observe
    assert getattr(result, "observation", None) is None
    observe.assert_not_called()


def test_loop_reuses_only_the_fresh_observation_returned_by_wait_for():
    found = observation()
    decisions = [{"action": "wait_for", "args": {"target": "ready"}}, FINISH]

    def execute(action, *args):
        if action == "finish":
            return finished()
        # Assignment keeps this regression reproducible before the new field.
        result = registry.ActionResult(True, "Ready is present")
        result.observation = found
        return result

    agent, _ = run_task(decisions, execute)
    assert agent._perceive.call_count == 1, "wait_for already performed the second read"
    assert agent._verify_success.call_args.kwargs["obs"] is found
    turn = agent.brain.complete.call_args_list[-1].args[1][-1]["content"]
    assert '"Ready"' in turn and '"Initial"' not in turn


def test_actions_without_an_observation_still_refresh_after_each_change():
    decisions = [{"action": "click", "args": {"element": 0}}, FINISH]
    agent, _ = run_task(decisions, lambda action, *args: (
        finished() if action == "finish" else registry.ActionResult(True, "clicked")))
    assert agent._perceive.call_count == 2
    assert agent._maybe_image.call_count == 2


def benchmark():
    samples = []
    for _ in range(31):
        calls = Counter()
        root = tree(calls)
        start = time.perf_counter()
        found = walk(root)
        samples.append((time.perf_counter() - start) * 1000)
    print(json.dumps({
        "workload": "synthetic UIA: 180 offscreen + 100 chrome controls + page",
        "runs": 30, "warmup": 1,
        "median_ms": round(statistics.median(samples[1:]), 3),
        "min_ms": round(min(samples[1:]), 3),
        "max_ms": round(max(samples[1:]), 3),
        "name_reads_per_walk": calls["names"],
        "children_reads_per_walk": calls["children"],
        "elements": len(found),
        "samples_ms": [round(s, 3) for s in samples[1:]],
    }, indent=2))


if __name__ == "__main__":
    benchmark()
