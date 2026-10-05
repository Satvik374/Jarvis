"""The agentic loop: perceive -> think -> act, repeated until done.

This is the core of Jarvis. Given a natural-language task it:

  1. PERCEIVE - screenshot the desktop and build a labelled element list.
  2. THINK    - ask the brain for the single next action (JSON).
  3. ACT      - execute it against the live screen.
  4. observe the result, append it to the running conversation, and repeat
     until the model calls ``finish``/``ask`` or the step budget is hit.

Every step is logged via :class:`TrajectoryWriter` so real runs become data.
"""

from __future__ import annotations

import json
import queue
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from ..config import Config
from ..perception import screen as screen_mod
from ..perception import elements as elem_mod
from ..perception import annotate as annotate_mod
from ..tools import registry
from ..utils import logging as log
from ..utils.paths import state_root
from .brain import Brain, BrainError, complete_with_retry
from .prompts import (build_system_prompt, parse_decision, format_observation,
                      format_decision, _extract_json)
from .subagent import agents_note
from .trajectory import Trajectory, TrajectoryWriter, Step
from .tree_of_thought import SelfHealingDirector


_IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")

#: A request to look through the camera is a task however softly it is phrased.
#: "look through the camera" names no UI target and starts with a word too
#: ambiguous for the verb list ("look, I need help"), so it used to fall to the
#: chat path - which has no tools at all, and answered that Jarvis cannot see,
#: exactly the wrong answer once the 'camera' action exists.
_CAMERA_INTENT = re.compile(
    r"\b(camera|webcam|photo of me|look at me|see me|how do i look|"
    r"what do i look like)\b",
    re.IGNORECASE)


#: A request to switch the camera hand-mouse control on or off is a task
#: however softly it is phrased. "enable mouse control" names no UI target and
#: starts with a word the verb list does not carry, so it fell to the chat path
#: - which has no tools at all, and answered that Jarvis cannot control the
#: mouse, exactly the wrong answer now that the 'mouse_control' action exists.
#: The gate covers both names the action goes by ("mouse control", "hand
#: control") because the user, the schema summary and the voice agent's
#: delegated task all say it differently.
_MOUSE_CONTROL_INTENT = re.compile(
    r"\b(?:mouse|hand|gesture|cursor)\b[\s/-]*"
    r"(?:and\s+(?:mouse|hand)\s+)?control\b|"
    r"\bcontrol\s+(?:the\s+|my\s+)?(?:mouse|pointer|cursor)\b",
    re.IGNORECASE)


#: The user's speed switch, written at the end of a task: ``-yolo``.
#: Speed over proof - act now, do not re-read the screen, do not verify. It is
#: recognised only as the LAST token, so the word "yolo" in a sentence stays a
#: word, and it is stripped from the task whatever the mode, so the model is
#: never asked to interpret it and chat memory never accumulates it.
_YOLO_TAIL = re.compile(r"(?:^|\s)--?yolo\s*$", re.IGNORECASE)


#: Injected into the system prompt for a yolo run. It deliberately overrides the
#: standing rules (element ids, never guess pixels), because in this mode there
#: is no element list left to be right about - the rules that follow the screen
#: are exactly what the mode removes.
_YOLO_NOTE = """

=== YOLO MODE (the task ended with "-yolo") ===
Speed over verification: this run does NOT re-read the screen. No new element
list, no screenshot, and no verdict at the end - you work from the screen state
you were given when the task started, which goes stale the moment your first
action changes anything. So:
  * Click a remembered target BY NAME - {"action":"click","args":{"coord":"<name>"}}
    - from the COORDINATES YOU ALREADY KNOW list. That is one instant lookup:
    no screenshot, no element hunting, and it is the fastest click there is.
  * If the target is not remembered, decide the pixels yourself from the layout
    you know and click {"action":"click","args":{"x":..,"y":..}}. Do NOT call
    observe and do NOT wait for a fresh list - none is coming.
  * An element id from the frozen list is only trustworthy while nothing has
    changed yet. Once you have acted, prefer a saved name or your own x/y.
  * Prefer keyboard shortcuts and open_url over hunting for a button: every step
    costs a model round trip, and this mode exists to spend fewer of them.
  * Nothing checks your finish on screen, so check it yourself: a RESULT that
    does not say what you expected is a step to redo differently.
"""

#: What replaces the observation on a yolo turn after the first one. The element
#: list is the expensive part of the prompt and re-attaching a stale one would
#: both cost tokens and quietly claim the screen is current.
_YOLO_STALE = ("\n\n=== SCREEN STATE: NOT RE-READ (yolo mode) ===\n"
               "The element list above is from the START of the task and is now "
               "stale - no new one is coming and no screenshot is taken. Act on "
               "saved coordinates by name, on keyboard shortcuts, or on x/y you "
               "are sure of, and judge your own work from the RESULT lines.")


def parse_yolo(task: str) -> tuple[str, bool]:
    """Split a trailing ``-yolo`` off a task prompt: ``(task, yolo)``.

    A lone ``-yolo`` is a message, not a switch - there has to be something to
    act on - and anything else is left untouched, so this is safe to run over
    every prompt that reaches the loop.
    """
    if not isinstance(task, str):
        return task, False
    match = _YOLO_TAIL.search(task)
    if match is None:
        return task, False
    body = task[:match.start()].strip()
    if not body:
        return task, False
    return body, True


def _archive_screenshot(obs, shot, path: Path) -> None:
    """Save one diagnostic frame away from the model's critical path."""
    try:
        annotate_mod.annotate(obs, shot, path)
        obs.screenshot_path = str(path)
    except Exception:
        # Screenshot archival is diagnostic only and must never affect a task.
        pass


class _ScreenshotArchiver:
    """Bounded, daemonized diagnostic writer.

    At most one frame is being written and one waits behind it. If disk I/O
    falls behind, newer diagnostic frames are skipped before copying their
    multi-megabyte images; model vision is never affected.
    """

    def __init__(self, capacity: int = 2):
        self._slots = threading.BoundedSemaphore(max(1, capacity))
        self._queue: queue.Queue = queue.Queue(maxsize=max(1, capacity))
        self._start_lock = threading.Lock()
        self._worker: threading.Thread | None = None

    def submit(self, obs, shot, path: Path) -> bool:
        if not self._slots.acquire(blocking=False):
            return False
        try:
            archival_shot = screen_mod.Screenshot(
                image=shot.image.copy(),
                width=shot.width,
                height=shot.height,
            )
            self._ensure_worker()
            self._queue.put_nowait((obs, archival_shot, path))
            return True
        except Exception:
            self._slots.release()
            return False

    def _ensure_worker(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        with self._start_lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._run,
                daemon=True,
                name="jarvis-screenshot",
            )
            self._worker.start()

    def _run(self) -> None:
        while True:
            obs, shot, path = self._queue.get()
            try:
                _archive_screenshot(obs, shot, path)
            finally:
                self._queue.task_done()
                self._slots.release()


_SCREENSHOT_ARCHIVER = _ScreenshotArchiver()


def _asked_to_stop(result: Any) -> bool:
    """Whether a step result explicitly asked for this session to end.

    ``is True`` rather than a truthiness test, deliberately: ending the session
    is the one decision that must never be taken by accident, and a duck-typed
    result - a test double, a wrapper, anything whose attribute is merely
    truthy - is not the declared signal. Only the real boolean counts.
    """
    return getattr(result, "stop_session", False) is True


def _find_image(task: str):
    """Return a PIL image for the first existing image-file path in the prompt.

    Lets the user attach an image by simply including its path (drag & drop a
    file into the console pastes the quoted path). The path is left in the
    prompt text so tasks that operate ON the file ("delete photo.png") still
    carry the full instruction.
    """
    for m in re.finditer(r'"([^"]+)"|\'([^\']+)\'|(\S+)', task):
        cand = next(g for g in m.groups() if g is not None)
        if not cand.lower().endswith(_IMG_EXTS):
            continue
        p = Path(cand)
        if not p.is_file():
            continue
        try:
            from PIL import Image  # type: ignore

            img = Image.open(p)
            img.load()
            return img
        except Exception as exc:
            log.warn(f"could not load image {p.name}: {exc}")
            return None
_ACTIVE_AGENT: Agent | None = None
_ACTIVE_AGENT_LOCK = threading.Lock()


def get_active_agent() -> Agent | None:
    with _ACTIVE_AGENT_LOCK:
        return _ACTIVE_AGENT


def cancel_active_agent() -> bool:
    """Abort any currently executing agent task immediately."""
    with _ACTIVE_AGENT_LOCK:
        if _ACTIVE_AGENT is not None:
            _ACTIVE_AGENT.cancel()
            return True
    return False


class Agent:
    #: The speed switch, defaulted at CLASS level as well as in __init__ because
    #: a couple of callers build an Agent with object.__new__ (tests, and the
    #: Discord router) and must still read a sane value: off.
    _yolo = False

    def __init__(self, brain: Brain, cfg: Config):
        self.brain = brain
        self.cfg = cfg
        self.cancel_event = threading.Event()
        #: This run's speed switch, set by run() from a trailing "-yolo".
        self._yolo = False
        state_dir = state_root()
        self.memory_path = state_dir / "memory.txt"
        # Conversational memory: only (user prompt, Jarvis response) pairs,
        # persisted across sessions. Kept separate from the fact memory.txt so
        # thoughts and actions never leak into the chat history.
        self.chat_path = state_dir / "chat_memory.jsonl"
        # Anchor internal data dirs to the project root so Jarvis writes to the
        # same place no matter which directory the `jarvis` command is run from.
        traj_dir = Path(cfg.data.trajectory_dir)
        if not traj_dir.is_absolute():
            traj_dir = state_dir / traj_dir
        self.writer = TrajectoryWriter(
            str(traj_dir), enabled=cfg.data.collect_trajectories)
        self._shot_dir = state_dir / "dataset" / "data" / "screenshots"
        from ..memory.manager import get_memory_manager
        self.memory_mgr = get_memory_manager(memory_path=self.memory_path)

    def cancel(self) -> None:
        """Signal the agent loop to abort the running task immediately."""
        self.cancel_event.set()
        log.warn("Agent cancel signal triggered.")


    # ------------------------------------------------------------------ #
    def run(self, task: str, asker=None, cancel_event: threading.Event | None = None,
            on_progress: Callable[[dict[str, Any]], None] | None = None) -> str:
        """Execute one task to completion; returns the final message.

        ``asker`` is an optional ``callable(question) -> answer`` wired by
        interactive frontends (typed console, voice). When set, an ``ask``
        action becomes a mid-task dialogue: the user's answer is fed back and
        the task continues. Without it (cron, one-shot CLI without a TTY) an
        ``ask`` ends the run with the question, as before.

        ``on_progress`` is an optional telemetry listener receiving structured
        event dicts for real-time live voice monitoring and supervisor narration.

        One straight-through attempt: there is no planning phase. The step loop
        is adaptive on every turn - it reads the screen, self-heals a failed
        action and re-decides - so an up-front plan (or a second strategy after
        a failure) only added latency and a second vocabulary for the same
        thing. A run that fails is reported to the user as a failure.

        ask/cancel/brain-error end the run immediately, exactly as before.
        """
        global _ACTIVE_AGENT
        with _ACTIVE_AGENT_LOCK:
            _ACTIVE_AGENT = self
        self.cancel_event.clear()
        effective_cancel = cancel_event or self.cancel_event

        # A trailing "-yolo" is the user's speed switch, parsed HERE so every
        # frontend - typed console, Discord, phone, cron - gets one behaviour
        # from one place. The task itself is left clean: the model is never
        # asked to interpret a flag, and the flag never reaches chat memory.
        task, self._yolo = parse_yolo(task)
        if self._yolo:
            log.warn("yolo mode: the screen is not re-read and the finish is not "
                     "verified - speed over proof.")

        def _notify(event_type: str, **kwargs: Any) -> None:
            if on_progress is not None:
                try:
                    payload = {"event": event_type, **kwargs}
                    on_progress(payload)
                except Exception as _cb_exc:
                    log.debug(f"Progress listener exception: {_cb_exc}")

        _notify("task_start", task=task)

        try:
            memory = self._read_memory(task=task)
            chat_note = self._chat_context()   # persistent user<->Jarvis history

            # Image input: an image-file path anywhere in the prompt attaches that
            # image to every model call for this run (alongside the screenshot).
            user_image = _find_image(task)
            if user_image is not None:
                log.info("attached image from prompt.")
                vision = self.brain.vision_state()
                if not vision.configured:
                    log.warn("vision is off - the attached image will be ignored "
                             "(':vision on' to enable).")
                elif not vision.usable:
                    log.warn(f"vision is paused ({vision.reason}) - the attached "
                             "image is ignored for now.")

            # Plain conversation (greeting, small talk, a question that needs no
            # computer access) -> reply directly with NO tools or perception.
            # Commands that clearly control the computer skip this entirely, and the
            # classifier is conservative, so existing control behaviour is untouched.
            if not self._looks_like_task(task):
                reply = self._maybe_chat(task, chat_note, image=user_image)
                if reply is not None:
                    self._append_chat(task, reply)
                    _notify("finish", result=reply, success=True, chat=True)
                    return reply

            if effective_cancel.is_set():
                log.warn("Task cancelled by user before it started.")
                _notify("cancelled", task=task)
                return "Task cancelled by user."

            log.step(f"Task: {task}")
            final_message = "The task did not complete."
            task_succeeded = False

            # We start with the initial observation
            obs = self._perceive()

            from .. import mcp
            from .. import remote
            from .. import skills
            from ..memory import coordinates
            from ..tools import connectors
            from ..tools import tool_synthesis
            # Coordinates Jarvis already knows for this task, surfaced next
            # to the other lookup-before-you-work notes: a control found on
            # an earlier run costs a name here instead of a screenshot and a
            # UI-Automation walk (or, on the phone, an image upload).
            try:
                window = getattr(obs, "active_window", "") or ""
            except Exception:
                window = ""
            system = (build_system_prompt(memory) + chat_note + agents_note()
                      + connectors.note() + mcp.tools_note() + remote.note(self.cfg)
                      + tool_synthesis.synthesized_tools_prompt_note(task)
                      + skills.note(task)
                      + coordinates.note(task, window)
                      + (_YOLO_NOTE if self._yolo else ""))

            traj = Trajectory(task=task, backend=self.cfg.brain.backend,
                              model=self.cfg.brain.model)
            task_msg = f"TASK: {task}"
            if user_image is not None:
                task_msg += ("\n(The user attached an image. Each turn, the "
                             "FIRST image is that attachment; a second image, "
                             "if present, is the current screen.)")
            messages: list[dict] = [{"role": "user", "content": task_msg}]

            director = SelfHealingDirector(
                config_self_healing=getattr(self.cfg.safety, "self_healing", True),
                max_healing_attempts=getattr(self.cfg.safety, "max_healing_attempts", 3),
            )
            off_script = 0     # consecutive non-action replies from the model
            last_changed = True    # did the previous action change the screen?
            remote_image = None   # authenticated image returned by a paired device

            for step_i in range(1, self.cfg.safety.max_steps + 1):
                if effective_cancel.is_set():
                    log.warn("Task cancelled by user.")
                    final_message = "Task cancelled by user."
                    traj.outcome = "cancelled"
                    _notify("cancelled", task=task, step=step_i)
                    break

                _notify("step_start", step=step_i, max_steps=self.cfg.safety.max_steps)

                # Browser/remote tools already supplied the image for this
                # turn. Do not capture or archive an unrelated desktop frame
                # only to discard it. Clearing that image restores fresh local
                # capture on the next turn; no old desktop frame is cached.
                image = (remote_image if remote_image is not None
                         else self._maybe_image(obs, step_i))
                screen_image = image
                if user_image is not None:
                    image = [user_image] + ([image] if image is not None else [])
                # yolo attaches the observation once, as the truth it is at
                # step 1; every turn after that is told the state is stale rather
                # than shown it again.
                fresh = not self._yolo or step_i == 1
                messages_for_turn = self._with_observation(messages, obs,
                                                           fresh=fresh)

                try:
                    with log.spinner(f"thinking (step {step_i}/{self.cfg.safety.max_steps})"):
                        # Task-critical call: waiting out a capacity window
                        # (~95s) is worth it because the whole task dies
                        # without this step, and a screenshot the failing
                        # route refuses is dropped on the retry.
                        raw = complete_with_retry(self.brain, system,
                                                  messages_for_turn,
                                                  image=image,
                                                  task_patience=True)
                except KeyboardInterrupt:
                    # Ctrl+C mid-task: keep the partial trajectory as data
                    # instead of silently losing the whole run.
                    traj.outcome = "interrupted"
                    traj.summary = "interrupted by user"
                    self.writer.save(traj)
                    _notify("cancelled", task=task, step=step_i)
                    raise
                except Exception as exc:
                    log.error(f"brain error: {exc}")
                    # The log line above keeps the technical detail; what the
                    # user is told (and hears) is the plain-English version.
                    final_message = log.friendly_error(exc)
                    traj.outcome = "error"
                    # Record WHY, so failure analysis over trajectories can see
                    # the actual error instead of a bare "error" label.
                    traj.summary = f"brain error: {exc}"[:300]
                    _notify("error", error=str(exc), step=step_i)
                    break

                if effective_cancel.is_set():
                    log.warn("Task cancelled by user.")
                    final_message = "Task cancelled by user."
                    traj.outcome = "cancelled"
                    _notify("cancelled", task=task, step=step_i)
                    break

                decision = parse_decision(raw)
                if decision.thought:
                    log.think(decision.thought)

                # The parser could not extract a real action (prose reply or a
                # hallucinated action name). Do NOT treat that as a finish -
                # push back once and let the model correct itself.
                if decision.fallback:
                    off_script += 1
                    if off_script >= 3:
                        log.warn("model went off-script 3 times; abandoning the run.")
                        traj.outcome = "off_script"
                        break
                    log.warn("reply was not a valid action; asking the model to retry.")
                    messages.append({"role": "assistant", "content": decision.raw[:400]})
                    messages.append({"role": "user", "content":
                                     "RESULT: Your reply was not a valid action. Reply with "
                                     "exactly ONE JSON object: {\"thought\": ..., \"action\": "
                                     "<one of the listed actions>, \"args\": {...}}. If the task "
                                     "is complete, use the 'finish' action."})
                    continue
                off_script = 0

                _notify("step_action", step=step_i, max_steps=self.cfg.safety.max_steps,
                        thought=decision.thought, action=decision.action, args=decision.args)

                if not self._confirm(decision):
                    final_message = "Cancelled by user."
                    traj.outcome = "cancelled"
                    _notify("cancelled", task=task, step=step_i)
                    break

                if effective_cancel.is_set():
                    final_message = "Cancelled by user."
                    traj.outcome = "cancelled"
                    _notify("cancelled", task=task, step=step_i)
                    break

                try:
                    result = registry.execute(decision.action, decision.args,
                                              obs, self.cfg)
                except Exception as exc:
                    if _is_failsafe(exc):
                        log.warn("FAIL-SAFE: mouse moved to a screen corner - "
                                 "aborting the task.")
                        final_message = ("Aborted by fail-safe (mouse moved to "
                                         "a screen corner).")
                        traj.outcome = "failsafe"
                        _notify("error", error="Fail-safe triggered", step=step_i)
                        break
                    # Self-healing: an action crash is fed back to the model as
                    # a failed result so it can adapt, instead of killing the
                    # whole run with a traceback.
                    log.warn(f"action {decision.action} crashed: {exc}")
                    result = registry.ActionResult(
                        False, f"the {decision.action} action crashed with an "
                               f"internal error: {exc}. Try a different "
                               f"approach or different arguments.")
                if result.clear_image:
                    remote_image = None
                if result.image_path:
                    loaded_action_image = _find_image(f'"{result.image_path}"')
                    if loaded_action_image is not None and self.brain.vision_state().usable:
                        remote_image = loaded_action_image
                        if decision.action == "remote_task":
                            result.message += (
                                " The authenticated image on the next turn is the REMOTE DEVICE "
                                "screen; ignore the local desktop observation when interpreting it "
                                "and continue through remote_task only."
                            )
                log.act(f"{decision.action}({_fmt_args(decision.args)}) -> {result.message}")

                traj.add(Step(
                    active_window=obs.active_window,
                    elements=[e.to_dict() for e in obs.elements],
                    menu=obs.menu(), thought=decision.thought,
                    action=decision.action, args=decision.args,
                    result=result.message, ok=result.ok,
                ))

                _notify("step_result", step=step_i, max_steps=self.cfg.safety.max_steps,
                        thought=decision.thought, action=decision.action, args=decision.args,
                        result=result.message, ok=result.ok, finished=result.finished,
                        ask=bool(result.ask))

                if effective_cancel.is_set():
                    final_message = "Cancelled by user."
                    traj.outcome = "cancelled"
                    _notify("cancelled", task=task, step=step_i)
                    break

                # Record the exchange so the model has memory of what it did.
                messages.append({"role": "assistant",
                                 "content": format_decision(decision.thought,
                                                            decision.action,
                                                            decision.args)})
                messages.append({"role": "user",
                                 "content": f"RESULT: {result.message}"})

                if result.finished:
                    if result.ask:
                        # Interactive session: relay the answer and keep going.
                        _notify("ask", question=result.ask, step=step_i)
                        answer = self._ask_user(result.ask, asker)
                        if answer:
                            messages[-1]["content"] = (
                                f"RESULT: the user answered: {answer}")
                            if traj.steps:
                                traj.steps[-1].result = f"User answered: {answer}"
                            _notify("answer_received", question=result.ask, answer=answer, step=step_i)
                            log.jarvis(f"🎙️ [Communicating Agent]: Understood, proceeding with: '{answer}'")
                            obs = self._refresh(obs)
                            continue
                        # No one to answer: the question ends the run - it must
                        # reach the user, not be swallowed as a failure.
                        final_message = result.ask
                        traj.outcome = "ask"
                        traj.summary = final_message
                        _notify("finish", result=final_message, success=False, ask=True, step=step_i)
                        break

                    # Genuine finish: verify before calling it done. A finish the
                    # verifier cannot confirm is reported as unverified instead of
                    # being claimed as a success. The verdict gets this step's own
                    # observation and screenshot: ``finish`` changes nothing, so
                    # re-reading the screen here would only add a second full
                    # perception pass to a task that is already done.
                    verdict, reason = self._verify_success(
                        task, messages, obs=obs, image=screen_image)
                    traj.success = verdict
                    if verdict is False:
                        log.warn(f"verifier: task NOT actually complete - {reason}")
                        traj.outcome = "finish_unverified"
                        traj.summary = result.message
                        final_message = (f"{result.message} (note: I could not "
                                         f"verify this completed: {reason})")
                        break

                    final_message = result.message
                    traj.outcome = "finish"
                    traj.summary = final_message
                    task_succeeded = True
                    _notify("finish", result=final_message, success=True, step=step_i)
                    break

                if _asked_to_stop(result):
                    # The agent asked for the session itself to end. There is
                    # nothing to verify: the decision to stop is the outcome. The
                    # runtime that owns the session closes it once control returns
                    # to it.
                    final_message = result.message
                    traj.outcome = "session_stop"
                    traj.summary = final_message
                    _notify("session_stop", reason=final_message, step=step_i)
                    log.warn(f"session stop requested at step {step_i}: {final_message}")
                    break

                # yolo deliberately does not re-read the screen: a fresh
                # element list + OCR walk per step is the cost the mode
                # removes. The failed-action branch below still runs, so a
                # step that went wrong is still diagnosed and fed back.
                if result.needs_observe and not self._yolo:
                    before_win = obs.active_window
                    before = obs.active_window + "\n" + obs.menu()
                    editable = self._clicked_editable(decision, obs)
                    # wait_for may have just read the full screen to find its
                    # target. That observation is already fresh, not a cache
                    # from an earlier action. Other actions still re-perceive.
                    observed = getattr(result, "observation", None)
                    obs = (observed if isinstance(observed, elem_mod.Observation)
                           else self._perceive())
                    after_win = obs.active_window
                    last_changed = (obs.active_window + "\n" + obs.menu()) != before
                    if not last_changed and editable:
                        # Clicking a text/prompt box only sets focus + caret,
                        # which never shows up in the element list. That is
                        # success, not failure - tell the model to type, so it
                        # does not re-click the box forever thinking it missed.
                        messages[-1]["content"] += (
                            " (note: the text field is now focused - the element "
                            "list does not change when a field gains focus. This "
                            "is expected; proceed to type, do NOT click it again.)")
                    elif not last_changed:
                        # Explicit no-effect signal - without it a small model
                        # cannot tell that its click achieved nothing.
                        messages[-1]["content"] += (
                            " (note: the screen did NOT change after this "
                            "action - if that was unexpected, try a different "
                            "approach)")

                    # Tree-of-Thought & Self-Healing Diagnosis
                    if getattr(self.cfg.safety, "self_healing", True):
                        diag, repair = director.diagnose_and_guide(
                            thought=decision.thought,
                            action=decision.action,
                            args=decision.args,
                            is_ok=result.ok,
                            result_message=result.message,
                            screen_changed=last_changed,
                            before_window=before_win,
                            after_window=after_win,
                        )
                        note = director.get_healing_note(diag, repair)
                        if note:
                            messages[-1]["content"] += f"\n[{note}]"
                            if repair:
                                obs = self._perceive()
                else:
                    if getattr(self.cfg.safety, "self_healing", True) and not result.ok:
                        diag, repair = director.diagnose_and_guide(
                            thought=decision.thought,
                            action=decision.action,
                            args=decision.args,
                            is_ok=result.ok,
                            result_message=result.message,
                            screen_changed=False,
                            before_window=obs.active_window,
                            after_window=obs.active_window,
                        )
                        note = director.get_healing_note(diag, repair)
                        if note:
                            messages[-1]["content"] += f"\n[{note}]"
            else:
                final_message = (f"Reached the {self.cfg.safety.max_steps}-step "
                                 "limit without finishing.")
                traj.outcome = "step_limit"

            self.writer.save(traj)

            if task_succeeded:
                log.ok("Task completed.")
            else:
                log.warn(f"Task not completed: {final_message}")

            # Remember the exchange (prompt + response only - no thoughts).
            self._append_chat(task, final_message)
            log.pop(success=task_succeeded)   # audible "task finished" cue
            return final_message
        finally:
            # The switch is per-RUN, never per-agent: one Agent serves the console
            # and the Discord listener task after task, so a mode left set here
            # would silently blind every task that followed it.
            self._yolo = False
            with _ACTIVE_AGENT_LOCK:
                if _ACTIVE_AGENT is self:
                    _ACTIVE_AGENT = None



    # ------------------------------------------------------------------ #
    @staticmethod
    def _ask_user(question: str, asker) -> str | None:
        """Route a mid-task question to the live user via ``asker``. Returns
        their answer, or None when no asker is wired / they gave none."""
        if asker is None:
            return None
        try:
            return (asker(question) or "").strip() or None
        except Exception:
            return None

    def _verify_success(self, task: str, messages: list[dict], obs=None,
                        image=None) -> tuple:
        """Judge whether a claimed finish actually completed the task.

        Returns (verdict, reason): True/False, or (None, ...) when verification
        is disabled or inconclusive - inconclusive results still report the
        task as done, just without the "verified" claim.

        ``obs``/``image`` are the finishing step's own screen state, passed by
        the loop so the verdict does not pay for a second read of the same
        screen: ``finish`` changes nothing, so the frame the model decided on is
        the frame to judge. Callers with no fresh state (other frontends, tests)
        pass neither and get the original read-the-screen behaviour.
        """
        if self._yolo:
            # The user asked for speed instead of proof. The verdict costs a
            # capture, an observation and a model round trip, so it is skipped
            # here and the finish is accepted as-is.
            log.info("yolo: finish accepted without checking the screen.")
            return None, "yolo mode: verification skipped"

        if not self.cfg.data.verify_success:
            return None, "verification disabled"

        if obs is None:
            obs = self._perceive()
        # A vision brain must SEE the final screen: things like a playing video
        # barely show up in the UIA text menu, and a text-only verdict wrongly
        # rejects real successes (which then makes the agent redo/undo the task).
        if image is None and self.brain.vision_state().usable:
            try:
                image = screen_mod.capture().image
            except Exception:
                pass
        # Last few action/result exchanges give the verifier the context.
        recent = [m["content"] for m in messages[-8:]]
        history = "\n".join(r[:200] for r in recent)
        system = (
            "You are a task-completion verifier for a desktop automation agent. "
            "Given the task, the agent's recent actions, and the current screen "
            "state" + (" (screenshot attached)" if image is not None else "") + ", "
            "judge whether the task was completed. Claiming success is not "
            "evidence by itself, but if the screen state is consistent with the "
            "task being done, answer true. Answer false ONLY when something "
            "clearly shows the task did NOT complete. Reply with ONLY one JSON "
            "object: {\"success\": true or false, \"reason\": \"<short>\"}"
        )
        user = (f"TASK: {task}\n\nRECENT ACTIONS AND RESULTS:\n{history}\n\n"
                f"CURRENT SCREEN:\nACTIVE WINDOW: {obs.active_window or '(desktop)'}\n"
                f"ELEMENTS:\n{obs.menu()}\n\nDid the task complete? Reply with the JSON verdict.")
        try:
            with log.spinner("verifying"):
                raw = self.brain.complete(system, [{"role": "user", "content": user}],
                                          image=image)
        except Exception as exc:
            log.warn(f"verifier unavailable: {exc}")
            return None, "verifier call failed"

        obj = _extract_json(raw)
        if not isinstance(obj, dict) or not isinstance(obj.get("success"), bool):
            return None, "unparseable verdict: success must be a JSON boolean"
        return obj["success"], str(obj.get("reason", ""))[:200]

    def _read_memory(self, task: str = "") -> str:
        try:
            if task and hasattr(self, "memory_mgr") and self.memory_mgr is not None:
                return self.memory_mgr.get_rag_context(task, max_chars=self.cfg.data.memory_max_chars)
            return (self.memory_path.read_text(encoding="utf-8")
                    if self.memory_path.exists() else "")
        except Exception as exc:
            log.warn(f"Failed to read memory file: {exc}")
            return ""

    def _remember_fact(self, fact: str, category: str = "fact") -> str:
        """Store a permanent fact in memory.txt so it persists forever."""
        from .memory import remember_fact
        return remember_fact(self.memory_path, fact, category)

    def _forget_fact(self, target: str) -> str:
        """Remove facts matching target from permanent memory."""
        from .memory import forget_fact
        return forget_fact(self.memory_path, target)

    # -- plain conversation (no tools) --------------------------------- #
    _TASK_VERBS = frozenset((
        "open", "close", "click", "type", "press", "scroll", "select", "copy",
        "paste", "cut", "run", "launch", "start", "play", "pause", "stop",
        "search", "find", "go", "goto", "navigate", "download", "upload",
        "save", "delete", "remove", "move", "drag", "switch", "maximize",
        "minimize", "screenshot", "focus", "enter", "write", "refresh",
        "reload", "zoom", "hover", "rightclick", "doubleclick",
        # software-specific verbs -> the task loop delegates to code_task.
        # Deliberately NOT here: make/create/fix/generate - everyday chat
        # words ("make me a meal plan") that must reach the chat classifier;
        # it routes real computer work to the task loop anyway.
        "build", "code", "develop", "refactor", "debug", "implement",
        "install", "upgrade",
    ))
    _TASK_WRAPPERS = (
        ("can", "you"),
        ("could", "you"),
        ("would", "you"),
        ("will", "you"),
    )
    _CONCRETE_APPS = frozenset((
        "calculator", "calc", "chrome", "cmd", "edge", "excel", "explorer",
        "firefox", "notepad", "paint", "powershell", "settings", "spotify",
        "terminal", "vscode",
    ))
    _UI_OBJECT_TARGETS = frozenset((
        "app", "application", "browser", "file", "folder", "tab", "window",
    ))
    _UI_CONTROL_TARGETS = frozenset((
        "button", "checkbox", "control", "dropdown", "field", "icon", "input",
        "link", "menu", "option", "start", "tab", "textbox",
    ))
    _KEY_TARGETS = frozenset((
        "alt", "backspace", "ctrl", "delete", "enter", "esc", "escape", "f1",
        "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11",
        "f12", "shift", "space", "tab", "windows",
    ))
    _TEXT_ENTRY_TARGETS = frozenset((
        "box", "editor", "field", "input", "notepad", "textbox",
    ))
    _OPENABLE_EXTENSIONS = (
        ".bat", ".cmd", ".csv", ".doc", ".docx", ".exe", ".html", ".jpg",
        ".jpeg", ".pdf", ".png", ".ppt", ".pptx", ".ps1", ".py", ".txt",
        ".xls", ".xlsx",
    )

    def _looks_like_task(self, task: str) -> bool:
        if (_CAMERA_INTENT.search(task)
                or _MOUSE_CONTROL_INTENT.search(task)):
            return True
        # Imperative computer commands start with an action verb, so route them
        # straight to the control loop without paying for a classifier call.
        # ponytail: verb prefix, not NLP - high precision so no command is ever
        # mistaken for chat.
        words = [
            word.strip("!.,?:;")
            for word in task.strip().lower().lstrip("!.,?-").split()
        ]
        # The same clear command is often phrased politely. Strip only narrow
        # wrappers, then use verb-specific UI grammar. Natural-language verbs
        # are too ambiguous to fast-path alone ("press charges", "save me",
        # "develop a meal plan"), so anything unclear still uses the model
        # classifier and preserves conversational quality.
        stripped_wrapper = False
        while words:
            if words[0] in {"jarvis", "please"}:
                words = words[1:]
                stripped_wrapper = True
                continue
            wrapper = next(
                (prefix for prefix in self._TASK_WRAPPERS
                 if tuple(words[:len(prefix)]) == prefix),
                None,
            )
            if wrapper:
                words = words[len(wrapper):]
                stripped_wrapper = True
                continue
            break
        if not words:
            return False
        if not stripped_wrapper:
            return words[0] in self._TASK_VERBS

        verb, rest = words[0], words[1:]
        targets = set(rest)
        if verb in {"rightclick", "doubleclick", "screenshot"}:
            return True
        if verb == "click":
            obj = list(rest)
            while obj and obj[0] in {"a", "an", "on", "the"}:
                obj = obj[1:]
            return bool(obj and obj[0] in self._UI_CONTROL_TARGETS)
        if verb == "press":
            obj = list(rest)
            while obj and obj[0] in {"a", "an", "the"}:
                obj = obj[1:]
            return bool(
                obj
                and obj[0] in (
                    self._KEY_TARGETS | self._UI_CONTROL_TARGETS
                )
            )
        if verb == "scroll":
            if rest[:3] == ["down", "memory", "lane"]:
                return False
            return bool(
                rest
                and (
                    rest[0] in {"up", "down", "left", "right"}
                    or (
                        rest[0] == "page"
                        and len(rest) > 1
                        and rest[1] in {"up", "down"}
                    )
                )
            )
        if verb in {"type", "paste"}:
            if "out" in targets:
                return False
            for index, word in enumerate(rest):
                if word not in {"in", "into"}:
                    continue
                destination = list(rest[index + 1:])
                while destination and destination[0] in {
                    "a", "an", "my", "the",
                }:
                    destination = destination[1:]
                if (
                    destination
                    and destination[0] in self._TEXT_ENTRY_TARGETS
                ):
                    return True
            return False
        if verb in {"open", "close", "switch", "launch"}:
            obj = list(rest)
            while obj and obj[0] in {
                "a", "an", "my", "the", "to", "your",
            }:
                obj = obj[1:]
            if not obj or obj[0] in {"with", "into", "by"}:
                return False
            return (
                (
                    obj[0] in self._CONCRETE_APPS
                    and (
                        len(obj) == 1
                        or (
                            len(obj) == 2
                            and obj[1] in {
                                "app", "application", "browser", "tab", "window",
                            }
                        )
                    )
                )
                or (
                    obj[0] in self._UI_OBJECT_TARGETS
                    and len(obj) == 1
                )
                or (
                    obj[0] == "microsoft"
                    and len(obj) == 2
                    and obj[1] == "word"
                )
                or obj[0].startswith(("http://", "https://", "www."))
                or obj[0].endswith(self._OPENABLE_EXTENSIONS)
            )
        if verb in {"maximize", "minimize"}:
            obj = list(rest)
            while obj and obj[0] in {"a", "an", "the", "my"}:
                obj = obj[1:]
            return bool(
                obj
                and (
                    (
                        obj[0] in self._CONCRETE_APPS
                        and (
                            len(obj) == 1
                            or (
                                len(obj) == 2
                                and obj[1] == "window"
                            )
                        )
                    )
                    or (
                        obj[0] in self._UI_OBJECT_TARGETS
                        and len(obj) == 1
                    )
                )
            )
        return False

    def _maybe_chat(self, task: str, chat_note: str = "",
                    image=None) -> str | None:
        """Answer plain conversation directly, with NO tools or perception.

        Returns a friendly reply when the message is ordinary chat, or None
        when it is a computer task (the caller then runs the normal loop).
        Conservative: on any doubt or error it returns None so the existing
        computer-control behaviour always wins.
        """
        if not self.cfg.brain.conversational:
            return None
        # The conversational gate normally avoids loading task-only context,
        # but it must know about paired devices.  Otherwise a question such as
        # "can you control my Office PC?" can receive the outdated local-only
        # answer before the normal task loop gets a chance to use remote_task.
        from .. import remote
        remote_note = remote.note(self.cfg)
        rag_note = ""
        if hasattr(self, "memory_mgr") and self.memory_mgr is not None:
            try:
                rag_note = self.memory_mgr.get_rag_context(task, max_chars=1200)
            except Exception:
                rag_note = ""
        system = (
            "You are JARVIS - a warm, quick-witted personal assistant with the "
            "easy polish of a trusted aide and a dry sense of humour, who can "
            "also control the user's Windows computer. You talk like a person, "
            "not a program: natural, concise, personable, and you refer back "
            "to earlier conversation like a colleague who remembers. "
            "Decide whether the user's message is "
            "ordinary CONVERSATION you can answer with no access to their "
            "computer (greetings, thanks, small talk, general questions like "
            "'who are you' or 'what is 2+2'), or a TASK that needs you to look "
            "at or control their computer (open/click/type/search/play/read the "
            "screen, look through their camera or describe something in the room "
            "in front of it, switch the camera hand-mouse control on or off, "
            "anything on their machine). If unsure, choose "
            "task.\n"
            "If the user asks you to remember something or provides a fact/preference to keep, "
            'include "remember": "<fact to store forever>" in your response JSON.\n'
            "Reply with ONE JSON object and nothing else:\n"
            '  {"mode":"chat","reply":"<friendly reply>","remember":"<optional fact to store forever>"}\n'
            '   or   {"mode":"task"}'
            + remote_note
            + (rag_note if rag_note else "")
        )
        messages: list[dict] = []
        user_content = f"Recent conversation:{chat_note}\n\n{task}" if chat_note else task
        messages.append({"role": "user", "content": user_content})
        try:
            with log.spinner("thinking"):
                raw = self.brain.complete(system, messages, image=image)
        except Exception:
            return None
        obj = _extract_json(raw)
        if isinstance(obj, dict) and str(obj.get("mode", "")).lower() == "chat":
            reply = str(obj.get("reply", "")).strip()
            rem = str(obj.get("remember", "")).strip()
            if rem:
                self._remember_fact(rem)
            if reply:
                return reply
        return None

    # -- conversational memory: prompt + response only ----------------- #
    def _load_chat(self) -> list[dict]:
        if not self.chat_path.exists():
            return []
        out: list[dict] = []
        try:
            for line in self.chat_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        continue
        except Exception as exc:
            log.warn(f"Failed to read chat memory: {exc}")
        return out

    def _chat_context(self) -> str:
        """Recent conversation, injected so Jarvis has continuity across tasks
        and sessions. Contains only what the user said and what Jarvis replied.
        """
        turns = max(0, int(self.cfg.data.chat_history_turns or 0))
        # `[-0:]` is the whole list, so 0 turns must short-circuit to none.
        pairs = self._load_chat()[-turns:] if turns else []
        lines = []
        for p in pairs:
            u, j = (p.get("user") or "").strip(), (p.get("jarvis") or "").strip()
            if u:
                lines.append(f"User: {u}")
            if j:
                lines.append(f"Jarvis: {j}")
        if not lines:
            return ""
        return ("\n\n=== EARLIER CONVERSATION (context only; may be from previous "
                "sessions) ===\n" + "\n".join(lines) +
                "\n=========================================================")

    def _append_chat(self, user: str, response: str) -> None:
        rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
               "user": (user or "").strip(), "jarvis": (response or "").strip()}
        try:
            pairs = self._load_chat()
            pairs.append(rec)
            pairs = pairs[-200:]   # ponytail: hard cap so the log never grows unbounded
            with self.chat_path.open("w", encoding="utf-8") as fh:
                for p in pairs:
                    fh.write(json.dumps(p, ensure_ascii=False) + "\n")
        except Exception as exc:
            log.warn(f"Failed to save chat memory: {exc}")

    # ------------------------------------------------------------------ #
    def _perceive(self):
        obs = elem_mod.observe(
            max_elements=self.cfg.perception.max_elements,
            use_uia=self.cfg.perception.use_uia,
            use_ocr=self.cfg.perception.use_ocr,
        )
        log.info(f"perceived {len(obs.elements)} elements "
                 f"in '{obs.active_window or 'desktop'}'")
        return obs

    def _refresh(self, obs):
        """A fresh observation - or, in yolo, the one we already have.

        Re-reading the screen costs a UI-Automation walk, an OCR pass and a
        capture; removing that per-step cost is the whole point of yolo, so the
        decision lives here instead of at each call site that used to call
        ``_perceive`` directly.
        """
        return obs if self._yolo else self._perceive()

    def _maybe_image(self, obs, step_i: int):
        """Capture (and optionally annotate) a screenshot when needed.

        "Needed" is the brain's answer, not the setting's: while vision is
        paused an image would be discarded on the way out, so capturing and
        annotating one would only add its cost to every step of the task.
        """
        if self._yolo:
            # yolo: no frame at all. The capture, the annotation and the image
            # tokens are the per-step cost this mode exists to skip, and the
            # model is told it is working blind rather than handed a picture of
            # a screen that no longer looks like this.
            return None
        vision = self.brain.vision_state()
        if not (vision.usable or self.cfg.perception.save_screenshots):
            return None
        try:
            shot = screen_mod.capture()
        except Exception:
            return None
        if self.cfg.perception.save_screenshots:
            try:
                name = screen_mod.timestamped_name(f"step{step_i:02d}")
                path = self._shot_dir / name
                # Annotation + PNG encoding costs hundreds of milliseconds but
                # its output is only for diagnostics. The bounded worker copies
                # accepted frames; the untouched live image goes to the model.
                _SCREENSHOT_ARCHIVER.submit(
                    obs,
                    shot,
                    path,
                )
            except Exception:
                pass
        return shot.image if vision.usable else None

    def _with_observation(self, messages: list[dict], obs,
                          fresh: bool = True) -> list[dict]:
        """Append the current screen state to the latest user turn.

        ``fresh=False`` is a yolo turn after the first: the screen has not been
        re-read, so the state is announced as stale instead of being re-sent as
        if it were current.
        """
        state = (format_observation(obs.active_window, obs.screen_size, obs.menu())
                 if fresh else _YOLO_STALE)
        out = [dict(m) for m in messages]
        if out and out[-1].get("role") == "user":
            existing = str(out[-1].get("content", ""))
            out[-1]["content"] = (existing + "\n\n" + state).strip() if existing else state
        else:
            out.append({"role": "user", "content": state})
        return out

    # Roles that accept typed text: clicking one to focus it is a success even
    # though the element list is unchanged (focus/caret never show up in UIA).
    _EDITABLE_ROLES = frozenset({"Edit", "Document", "ComboBox"})

    def _clicked_editable(self, decision, obs) -> bool:
        if decision.action not in {"click", "double_click", "triple_click"}:
            return False
        el_id = decision.args.get("element")
        if el_id is None:
            return False
        try:
            el = obs.by_id(int(el_id))
        except (TypeError, ValueError):
            return False
        return el is not None and el.role in self._EDITABLE_ROLES

    def _confirm(self, decision) -> bool:
        if not self.cfg.safety.confirm_each_action:
            return True
        if decision.action in {"finish", "ask", "observe", "wait"}:
            return True
        try:
            ans = input(f"    run {decision.action}({_fmt_args(decision.args)})? "
                        f"[Y/n] ").strip().lower()
        except EOFError:
            # Confirmation was explicitly requested. A closed/noninteractive
            # input channel is not consent to run the action.
            return False
        return ans in {"", "y", "yes"}


def _is_failsafe(exc: BaseException) -> bool:
    """True for pyautogui's FailSafeException, matched by name so this module
    never has to import pyautogui itself."""
    return type(exc).__name__ == "FailSafeException"


def _fmt_args(args: dict) -> str:
    """Compact one-line arg display; long values (file content, big text) are
    truncated so a write_file never floods the console."""
    parts = []
    for k, v in (args or {}).items():
        r = repr(v)
        if len(r) > 80:
            r = r[:77] + "..."
        parts.append(f"{k}={r}")
    return ", ".join(parts)
