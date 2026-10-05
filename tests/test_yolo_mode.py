"""The "-yolo" switch: act now, do not re-read the screen, do not verify.

The user writes it at the end of a task ("open notepad -yolo") when they want
speed instead of proof. That is a real trade - the per-step observation and the
verifier are exactly what make an action safe to take blind - so the mode must
exist only where the user asked for it, only for that one run, and it must not
be told to the model as part of the task.
"""

from __future__ import annotations

import json
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

from jarvis.agent import loop
from jarvis.agent.loop import Agent, parse_yolo
from jarvis.config import Config
from jarvis.perception.elements import Element, Observation
from jarvis.tools import registry

#: Something only the element list would carry, so "was the screen re-sent as
#: current?" is answerable by counting it.
MARKER = "SEND_MARKER_XYZ"


def _obs(window: str = "Notepad"):
    return Observation(
        elements=[Element(0, "Button", MARKER, (10, 10, 40, 30), (25, 20))],
        screen_size=(1920, 1080),
        active_window=window,
    )


class ScriptedBrain:
    """A provider stand-in: the classifier by shape, the steps by script."""

    def __init__(self, *decisions):
        self.decisions = list(decisions)
        self.systems: list[str] = []
        self.turns: list[list[dict]] = []
        self.images: list[object] = []
        #: Verifier prompts answered - counted here rather than read off
        #: ``systems``, because answering one means not forwarding it inward.
        self.verified = 0

    def complete(self, system, messages, image=None, **_kwargs):
        self.systems.append(str(system))
        self.turns.append([dict(m) for m in messages])
        self.images.append(image)
        if "ordinary CONVERSATION" in str(system):
            return '{"mode": "task"}'
        if self.decisions:
            step = self.decisions.pop(0)
            return step if isinstance(step, str) else json.dumps(step)
        return json.dumps({"thought": "done", "action": "finish",
                           "args": {"summary": "Opened it."}})

    def vision_state(self):
        return SimpleNamespace(usable=False, configured=False, reason="")


def _build(brain) -> Agent:
    cfg = Config()
    cfg.data.collect_trajectories = False
    cfg.data.trajectory_dir = tempfile.mkdtemp()
    return Agent(brain, cfg)


def _click_saved_coordinate():
    """The step a yolo run is expected to take: one click, by remembered name."""
    return [{"thought": "click the saved button", "action": "click",
             "args": {"coord": "notepad-send-button"}}]


def _ok(action, args, obs, cfg):
    if action == "finish":
        return registry.ActionResult(True, "Opened it.", finished=True,
                                     needs_observe=False)
    return registry.ActionResult(True, f"{action} on {args}", needs_observe=True)


def _fails(action, args, obs, cfg):
    if action == "finish":
        return registry.ActionResult(True, "Gave up.", finished=True,
                                     needs_observe=False)
    return registry.ActionResult(False, "element not found on screen",
                                 needs_observe=True)


def _with_verdict(brain):
    """Answer the verifier with a plain yes, so a run can finish for real."""
    inner = brain.complete

    def complete(system, messages, image=None, **kwargs):
        if "task-completion verifier" in str(system):
            brain.verified += 1
            return '{"success": true, "reason": "it is on screen"}'
        return inner(system, messages, image=image, **kwargs)

    return complete


def _run(agent, task, execute=_ok):
    """One run against a faked desktop: no capture, no clicks, no windows."""
    with patch.object(agent, "_perceive", side_effect=lambda: _obs()) as perceive, \
         patch("jarvis.tools.registry.execute", side_effect=execute):
        result = agent.run(task)
    return result, perceive


def _turns(brain) -> list[str]:
    """Every turn's text, one string per model call."""
    return ["\n".join(str(m.get("content", "")) for m in turn)
            for turn in brain.turns]


# --------------------------------------------------------------------------- #
# reading the flag
# --------------------------------------------------------------------------- #

def test_the_flag_is_only_the_last_word_of_the_prompt():
    assert parse_yolo("open notepad -yolo") == ("open notepad", True)
    assert parse_yolo("Open Notepad -YOLO") == ("Open Notepad", True)
    assert parse_yolo("open notepad --yolo") == ("open notepad", True)
    assert parse_yolo("open notepad") == ("open notepad", False)
    # A word that merely starts like the switch is a word.
    assert parse_yolo("open notepad -yolooo") == ("open notepad -yolooo", False)
    # "yolo" in the middle of a sentence is part of the sentence.
    assert parse_yolo("put yolo in the filename") == \
        ("put yolo in the filename", False)
    assert parse_yolo("open the yolo folder -yolo") == ("open the yolo folder", True)
    # A switch with nothing to act on is a message, not a mode.
    assert parse_yolo("-yolo") == ("-yolo", False)
    assert parse_yolo("   -yolo  ") == ("   -yolo  ", False), \
        "a switch with nothing to act on is left exactly as written"


# --------------------------------------------------------------------------- #
# what the mode actually skips
# --------------------------------------------------------------------------- #

def test_a_yolo_run_reads_the_screen_exactly_once():
    """The observation is the per-step cost - an element walk, an OCR pass and a
    capture for every action - and removing it is the whole point, so exactly one
    is allowed: the one taken before the first step."""
    brain = ScriptedBrain(*_click_saved_coordinate())
    agent = _build(brain)

    _, perceive = _run(agent, "open notepad -yolo")

    assert perceive.call_count == 1
    assert brain.decisions == [], "the scripted step never ran"


def test_an_ordinary_run_reads_the_screen_again_after_each_action():
    """The control: only the flag may remove the re-read."""
    brain = ScriptedBrain(*_click_saved_coordinate())
    agent = _build(brain)

    with patch.object(agent, "_verify_success", return_value=(True, "done")):
        _, perceive = _run(agent, "open notepad")

    assert perceive.call_count == 2, "the screen is re-read after the click"


def test_the_verdict_is_judged_on_the_finishing_step_and_reads_nothing_again():
    """A finish changes nothing, so judging it must not pay for a third read.

    The verdict used to take its own observation and its own screenshot of the
    very screen the finishing step had just looked at - a perception pass and a
    capture per task, for a frame that could not differ.
    """
    brain = ScriptedBrain(*_click_saved_coordinate())
    brain.vision_state = lambda: SimpleNamespace(usable=True, configured=True,
                                                 reason="")
    brain.complete = _with_verdict(brain)
    agent = _build(brain)
    shots = [SimpleNamespace(image=Mock(name="frame-1")),
             SimpleNamespace(image=Mock(name="frame-2"))]

    with patch.object(agent, "_perceive", side_effect=lambda: _obs()) as perceive, \
         patch.object(loop.screen_mod, "capture", side_effect=shots) as capture, \
         patch("jarvis.tools.registry.execute", side_effect=_ok):
        agent.run("open notepad")

    assert brain.verified == 1, "the finish really was judged"
    assert perceive.call_count == 2, \
        "initial + after the click; the verdict reads nothing"
    assert capture.call_count == 2, "one frame per step, none for the verdict"
    assert brain.images[-1] is shots[1].image, \
        "judged on the frame the model had just been shown"


def test_a_yolo_run_takes_no_screenshot_at_all():
    """Even with screenshot saving switched on and a vision brain configured: a
    frame nobody looks at is pure latency, and the model is told it is blind."""
    brain = ScriptedBrain(*_click_saved_coordinate())
    brain.vision_state = lambda: SimpleNamespace(usable=True, configured=True,
                                                 reason="")
    agent = _build(brain)
    agent.cfg.perception.save_screenshots = True

    with patch.object(loop.screen_mod, "capture") as capture:
        _run(agent, "open notepad -yolo")

    capture.assert_not_called()


def test_a_yolo_finish_is_reported_without_asking_anyone():
    """The verdict costs a capture, an observation and a model round trip, so in
    this mode it is never asked for - and the finish is passed on as claimed
    rather than annotated with the caveat an unverified finish normally gets."""
    brain = ScriptedBrain(*_click_saved_coordinate())
    agent = _build(brain)
    assert agent.cfg.data.verify_success, "the control below needs it switched on"

    with patch.object(loop.screen_mod, "capture") as capture:
        result, _ = _run(agent, "open notepad -yolo")

    assert "Opened it." in result
    assert "could not verify" not in result
    # The verifier's own opening line is the evidence: the agents catalogue in
    # every system prompt mentions the word "verifier", so "verifier" alone
    # would match the prompt rather than the call.
    assert not any("task-completion verifier" in system for system in brain.systems)
    capture.assert_not_called()


def test_an_ordinary_finish_is_still_verified():
    """The control: the verifier is only skipped because of the flag."""
    brain = ScriptedBrain(*_click_saved_coordinate())
    brain.complete = _with_verdict(brain)
    agent = _build(brain)

    _run(agent, "open notepad")

    assert brain.verified == 1, "an ordinary finish is still judged on screen"


def test_the_yolo_verdict_is_inconclusive_so_nothing_can_be_rewarded():
    agent = _build(ScriptedBrain())
    agent._yolo = True

    verdict, reason = agent._verify_success("open notepad", [])

    assert verdict is None, "a run nobody looked at may not be called a success"
    assert "yolo" in reason


def test_a_failed_step_in_yolo_is_still_diagnosed():
    """Speed must not mean silence: the healing note is what stops the model
    repeating a step that failed, and it costs no screenshot."""
    brain = ScriptedBrain(*_click_saved_coordinate())
    agent = _build(brain)

    _run(agent, "open notepad -yolo", execute=_fails)

    assert any("SELF-HEALING" in text for text in _turns(brain)), \
        "a failure went past the model without a word"


# --------------------------------------------------------------------------- #
# what the model is told
# --------------------------------------------------------------------------- #

def test_the_model_is_told_it_is_working_from_a_stale_screen():
    """Two halves of the same lie to avoid: an element list that looks current,
    and rules that assume one exists. The note says neither is available."""
    brain = ScriptedBrain(*_click_saved_coordinate())
    agent = _build(brain)

    _run(agent, "open notepad -yolo")

    assert any("YOLO MODE" in system for system in brain.systems)


def test_an_ordinary_run_is_told_nothing_about_yolo():
    brain = ScriptedBrain(*_click_saved_coordinate())
    agent = _build(brain)

    with patch.object(agent, "_verify_success", return_value=(True, "done")):
        _run(agent, "open notepad")

    assert not any("YOLO MODE" in system for system in brain.systems)


def test_the_element_list_is_not_re_sent_as_if_it_were_current():
    """The list is the expensive part of the prompt, and re-sending a frozen one
    claims the screen is current. One run attaches it once; every turn after is
    told it is stale instead."""
    brain = ScriptedBrain(*_click_saved_coordinate())
    agent = _build(brain)

    _run(agent, "open notepad -yolo")

    assert sum(MARKER in text for text in _turns(brain)) == 1
    assert any("NOT RE-READ" in text for text in _turns(brain))


def test_an_ordinary_run_attaches_the_elements_to_every_turn():
    """The control: the stale marker is yolo's, not a change to the prompt."""
    brain = ScriptedBrain(*_click_saved_coordinate())
    agent = _build(brain)

    with patch.object(agent, "_verify_success", return_value=(True, "done")):
        _run(agent, "open notepad")

    assert sum(MARKER in text for text in _turns(brain)) == 2
    assert not any("NOT RE-READ" in text for text in _turns(brain))


def test_the_flag_never_reaches_the_model_or_the_chat_log():
    """It is a switch, not part of the task: a model asked to interpret "-yolo"
    would spend a step on it, and a chat log that accumulated it would show it
    forever after."""
    brain = ScriptedBrain(*_click_saved_coordinate())
    agent = _build(brain)

    with patch.object(agent, "_append_chat") as remember:
        result, _ = _run(agent, "open notepad -yolo")

    assert "-yolo" not in result
    # The note itself names the flag - that is the mode being explained, not the
    # task being handed over - so what must be clean is every turn the model
    # reads as the task, and the log written from it.
    assert not any("-yolo" in text for text in _turns(brain))
    assert remember.call_args.args[0] == "open notepad"


# --------------------------------------------------------------------------- #
# scope of the switch
# --------------------------------------------------------------------------- #

def test_the_switch_does_not_survive_into_the_next_task():
    """Both the console and the Discord listener reuse one Agent, so a switch
    left set would silently blind every task that followed it."""
    brain = ScriptedBrain(*_click_saved_coordinate())
    agent = _build(brain)

    _, first = _run(agent, "open notepad -yolo")
    assert agent._yolo is False, "the switch is per-run, not per-agent"
    brain.decisions = _click_saved_coordinate()      # the second task's one step
    with patch.object(agent, "_verify_success", return_value=(True, "done")):
        _, second = _run(agent, "open notepad")

    assert first.call_count == 1
    assert second.call_count == 2, "the next task must read the screen again"
    assert not any("YOLO MODE" in system for system in brain.systems[-2:])


def test_a_task_that_merely_mentions_yolo_is_untouched():
    brain = ScriptedBrain(*_click_saved_coordinate())
    agent = _build(brain)

    with patch.object(agent, "_verify_success", return_value=(True, "done")):
        _, perceive = _run(agent, "open the yolo folder")

    assert agent._yolo is False
    assert perceive.call_count == 2, "no flag means no mode"


def test_an_agent_built_without_init_still_reads_the_switch_as_off():
    """Some callers build an Agent with ``object.__new__`` (tests, the Discord
    router), so an unset switch must READ as off rather than not exist - the
    guard is the first line of both ``_maybe_image`` and ``_verify_success``."""
    agent = object.__new__(Agent)

    assert agent._yolo is False
