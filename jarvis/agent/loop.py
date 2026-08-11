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

from ..config import Config
from ..perception import screen as screen_mod
from ..perception import elements as elem_mod
from ..perception import annotate as annotate_mod
from ..tools import registry
from ..utils import logging as log
from .brain import Brain, BrainError, complete_with_retry
from .prompts import (build_system_prompt, parse_decision, format_observation,
                      format_decision, _extract_json)
from .subagent import agents_note
from .trajectory import Trajectory, TrajectoryWriter, Step


_IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")


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
    return None


class Agent:
    def __init__(self, brain: Brain, cfg: Config):
        self.brain = brain
        self.cfg = cfg
        proj_root = Path(__file__).resolve().parent.parent.parent
        self.memory_path = proj_root / "memory.txt"
        # Conversational memory: only (user prompt, Jarvis response) pairs,
        # persisted across sessions. Kept separate from the learned-plan
        # memory.txt so thoughts and plans never leak into the chat history.
        self.chat_path = proj_root / "chat_memory.jsonl"
        # Anchor internal data dirs to the project root so Jarvis writes to the
        # same place no matter which directory the `jarvis` command is run from.
        traj_dir = Path(cfg.data.trajectory_dir)
        if not traj_dir.is_absolute():
            traj_dir = proj_root / traj_dir
        self.writer = TrajectoryWriter(
            str(traj_dir), enabled=cfg.data.collect_trajectories)
        self._shot_dir = proj_root / "dataset" / "data" / "screenshots"

    # ------------------------------------------------------------------ #
    def _generate_plans(self, task: str, memory: str = "") -> list[dict]:
        # Exploit phase of the learning loop: if this task was already learned,
        # reuse the remembered approach and skip the (slow) planning call - the
        # full learned entry is already injected into the system prompt.
        if self._memory_has(task, memory):
            log.info("Task found in memory - using the learned approach directly.")
            return [{"name": "Learned Plan (from memory)",
                     "description": "Follow the previously successful approach "
                                    "recorded in PERSISTENT MEMORY for this exact task.",
                     "from_memory": True}]

        # Lazy planning: the step loop is already adaptive, so most tasks finish
        # on a direct attempt. Skip the up-front brainstorming LLM call (a full
        # round-trip of latency before Jarvis does anything) and only pay for
        # alternative plans if this first try fails (see the loop below).
        return [{"name": "Direct Attempt",
                 "description": "Execute the task directly using standard "
                                "operations, choosing each action from the "
                                "current screen state.",
                 "provisional": True}]

    def _brainstorm_plans(self, task: str) -> list[dict]:
        """Ask the brain for alternative strategies. Only called after the
        direct attempt fails, so its latency is off the common success path."""
        system_prompt = (
            "You are a strategic planning assistant. Propose up to 3 distinct, alternative plans "
            "to accomplish the user's desktop automation task. "
            "Format your reply as a JSON list of objects, each containing 'name' and 'description' keys. "
            "For example:\n"
            "[\n"
            "  {\"name\": \"Plan 1: Via Start Menu Search\", \"description\": \"Press the win key, type the app name, press enter, then...\"},\n"
            "  {\"name\": \"Plan 2: Via Run Dialog\", \"description\": \"Press win+r, type the executable name, press enter, then...\"}\n"
            "]\n"
            "Provide ONLY the JSON list. Do not include markdown formatting or extra text."
        )
        messages = [{"role": "user", "content": f"Task: {task}"}]
        try:
            with log.spinner("planning"):
                raw = self.brain.complete(system_prompt, messages)
            raw_clean = raw.strip()
            if raw_clean.startswith("```"):
                raw_clean = raw_clean.split("```", 2)[1]
                if raw_clean.startswith("json"):
                    raw_clean = raw_clean[4:]
            raw_clean = raw_clean.strip()
            plans = json.loads(raw_clean)
            # Tolerate common shapes: {"plans": [...]} and a single plan object.
            if isinstance(plans, dict):
                plans = plans.get("plans", plans)
                if isinstance(plans, dict):
                    plans = [plans]
            if isinstance(plans, list) and len(plans) > 0:
                validated = []
                for p in plans:
                    if isinstance(p, dict) and "name" in p and "description" in p:
                        validated.append(p)
                if validated:
                    return validated[:3]
        except Exception as exc:
            log.warn(f"Failed to generate custom plans via LLM: {exc}. Falling back to default plan.")

        return [{"name": "Default Action Path", "description": "Execute the task directly using standard operations."}]

    def run(self, task: str, asker=None) -> str:
        """Execute one task to completion; returns the final message.

        ``asker`` is an optional ``callable(question) -> answer`` wired by
        interactive frontends (typed console, voice). When set, an ``ask``
        action becomes a mid-task dialogue: the user's answer is fed back and
        the task continues. Without it (cron, one-shot CLI without a TTY) an
        ``ask`` ends the run with the question, as before.

        Reinforcement loop: generate candidate plans -> try each -> a plan is
        only *rewarded* (saved to persistent memory, trajectory labelled
        success=True) when the finish is genuine AND the verifier confirms the
        task actually completed on screen. ask/cancel/brain-error end the whole
        run immediately - they are not "plan failures" to retry past.
        """
        memory = self._read_memory()
        chat_note = self._chat_context()   # persistent user<->Jarvis history

        # Image input: an image-file path anywhere in the prompt attaches that
        # image to every model call for this run (alongside the screenshot).
        user_image = _find_image(task)
        if user_image is not None:
            log.info("attached image from prompt.")
            if not self.cfg.brain.use_vision:
                log.warn("vision is off - the attached image will be ignored "
                         "(':vision on' to enable).")

        # Plain conversation (greeting, small talk, a question that needs no
        # computer access) -> reply directly with NO tools/perception/planning.
        # Commands that clearly control the computer skip this entirely, and the
        # classifier is conservative, so existing control behaviour is untouched.
        if not self._looks_like_task(task):
            reply = self._maybe_chat(task, chat_note, image=user_image)
            if reply is not None:
                self._append_chat(task, reply)
                return reply

        log.step(f"Task: {task}")
        reexplored = False     # #3: only re-plan once after evicting a stale plan

        # Generate candidate solutions (or reuse a learned plan from memory)
        plans = self._generate_plans(task, memory)
        log.info(f"Generated {len(plans)} candidate plan(s) to try.")
        for idx, plan in enumerate(plans):
            log.info(f"  Plan {idx + 1}: {plan['name']}")

        final_message = "All candidate plans failed to complete the task."
        successful_plan = None
        # Only a VERIFIED success may be written to permanent memory. An
        # inconclusive verdict (verifier disabled, call failed, unparseable)
        # still reports the result to the user but must not be learned - that
        # is exactly the false-positive reward verification exists to prevent.
        rewardable = False
        last_failure = ""      # why the previous plan failed (feeds next attempt)
        abort_run = False      # ask/cancel/brain-error: stop everything

        # We start with the initial observation
        obs = self._perceive()

        idx = 0
        while idx < len(plans):
            plan = plans[idx]
            log.step(f"Trying Plan {idx + 1}/{len(plans)}: {plan['name']}")

            # Construct system prompt with memory and current plan info
            plan_note = f"\n\n=== CURRENT SOLUTION PLAN TO TRY ===\nPlan: {plan['name']}\nApproach: {plan['description']}"
            if idx > 0:
                plan_note += ("\nNote: A previous plan failed"
                              + (f" ({last_failure})" if last_failure else "")
                              + ". Try this alternative approach from the current screen state.")
            plan_note += "\n===================================="

            from .. import mcp
            from .. import remote
            from ..tools import connectors
            system = (build_system_prompt(memory) + chat_note + agents_note()
                      + connectors.note() + mcp.tools_note() + remote.note(self.cfg)
                      + plan_note)

            traj = Trajectory(task=task, backend=self.cfg.brain.backend,
                              model=self.cfg.brain.model)
            task_msg = f"TASK: {task}"
            if user_image is not None:
                task_msg += ("\n(The user attached an image. Each turn, the "
                             "FIRST image is that attachment; a second image, "
                             "if present, is the current screen.)")
            messages: list[dict] = [{"role": "user", "content": task_msg}]

            plan_succeeded = False
            off_script = 0     # consecutive non-action replies from the model
            last_changed = True    # did the previous action change the screen?
            remote_image = None   # authenticated image returned by a paired device

            for step_i in range(1, self.cfg.safety.max_steps + 1):
                local_image = self._maybe_image(obs, step_i)
                # Once a remote device returns a screenshot, keep that image in
                # view instead of the unrelated controller desktop. This avoids
                # grounding a mobile decision against the wrong screen.
                image = remote_image if remote_image is not None else local_image
                if user_image is not None:
                    image = [user_image] + ([image] if image is not None else [])
                messages_for_turn = self._with_observation(messages, obs)

                try:
                    with log.spinner(f"thinking (step {step_i}/{self.cfg.safety.max_steps})"):
                        raw = complete_with_retry(self.brain, system,
                                                  messages_for_turn, image=image)
                except KeyboardInterrupt:
                    # Ctrl+C mid-task: keep the partial trajectory as data
                    # instead of silently losing the whole run.
                    traj.outcome = "interrupted"
                    traj.summary = "interrupted by user"
                    self.writer.save(traj)
                    raise
                except Exception as exc:
                    log.error(f"brain error: {exc}")
                    final_message = f"Brain error: {exc}"
                    traj.outcome = "error"
                    # Record WHY, so failure analysis over trajectories can see
                    # the actual error instead of a bare "error" label.
                    traj.summary = f"brain error: {exc}"[:300]
                    abort_run = True      # same brain will fail for every plan
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
                        log.warn("model went off-script 3 times; abandoning this plan.")
                        traj.outcome = "off_script"
                        last_failure = "the model repeatedly replied without a valid action"
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

                if not self._confirm(decision):
                    final_message = "Cancelled by user."
                    traj.outcome = "cancelled"
                    abort_run = True      # the user said stop - stop everything
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
                        abort_run = True
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
                    loaded_remote_image = _find_image(f'"{result.image_path}"')
                    if loaded_remote_image is not None and self.cfg.brain.use_vision:
                        remote_image = loaded_remote_image
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
                        answer = self._ask_user(result.ask, asker)
                        if answer:
                            messages[-1]["content"] = (
                                f"RESULT: the user answered: {answer}")
                            obs = self._perceive()
                            continue
                        # No one to answer: the question ends the whole run -
                        # it must reach the user, not be swallowed as a
                        # "failed plan".
                        final_message = result.ask
                        traj.outcome = "ask"
                        traj.summary = final_message
                        abort_run = True
                        break

                    # Genuine finish: verify before rewarding.
                    verdict, reason = self._verify_success(task, messages)
                    traj.success = verdict
                    if verdict is False:
                        log.warn(f"verifier: task NOT actually complete - {reason}")
                        traj.outcome = "finish_unverified"
                        traj.summary = result.message
                        last_failure = f"it claimed success but verification found: {reason}"
                        # If every plan ends here, still tell the user what was
                        # claimed instead of a generic "all plans failed".
                        final_message = (f"{result.message} (note: I could not "
                                         f"verify this completed: {reason})")
                        break     # plan failed; try the next one

                    final_message = result.message
                    traj.outcome = "finish"
                    traj.summary = final_message
                    plan_succeeded = True
                    rewardable = verdict is True
                    break

                if result.needs_observe:
                    before = obs.active_window + "\n" + obs.menu()
                    editable = self._clicked_editable(decision, obs)
                    obs = self._perceive()
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
            else:
                final_message = f"Plan reached the {self.cfg.safety.max_steps}-step limit."
                traj.outcome = "step_limit"
                last_failure = "it hit the step limit without finishing"

            self.writer.save(traj)

            if abort_run:
                break
            if plan_succeeded:
                successful_plan = plan
                log.ok(f"Plan '{plan['name']}' succeeded!")
                break
            log.warn(f"Plan '{plan['name']}' failed.")

            # #3 un-learn: a plan we REUSED from memory just failed, so the
            # stored recipe is stale (UI changed) or was a false-positive
            # reward. Evict it and brainstorm fresh alternatives instead of
            # failing on a dead plan forever.
            if plan.get("from_memory") and not reexplored:
                self._evict_memory(task)
                memory = self._read_memory()            # stale entry gone, rest kept
                plans = self._brainstorm_plans(task)    # force fresh exploration
                log.info(f"Re-planned: {len(plans)} alternative(s) to try.")
                reexplored = True
                idx = 0
                obs = self._perceive()
                continue

            # Lazy planning: the direct attempt failed, so now (and only now)
            # spend the LLM call to brainstorm alternative strategies to try.
            if plan.get("provisional") and not reexplored:
                plans = self._brainstorm_plans(task)
                log.info(f"Direct attempt failed; brainstormed "
                         f"{len(plans)} alternative plan(s).")
                reexplored = True
                idx = 0
                obs = self._perceive()
                continue

            idx += 1
            # Refresh observation for the next plan start
            obs = self._perceive()

        # Reward: persist the plan only when the verifier CONFIRMED it worked.
        if successful_plan and rewardable:
            self._append_memory(task, successful_plan)

        # Remember the exchange (prompt + response only - no thoughts/plans).
        self._append_chat(task, final_message)
        log.pop(success=bool(successful_plan))   # audible "task finished" cue
        return final_message

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

    def _verify_success(self, task: str, messages: list[dict]) -> tuple:
        """Judge whether a claimed finish actually completed the task.

        Returns (verdict, reason): True/False, or (None, ...) when verification
        is disabled or inconclusive - inconclusive results are NOT rewarded
        with a memory write, but the task result is still reported.
        """
        if not self.cfg.data.verify_success:
            return None, "verification disabled"

        obs = self._perceive()
        # A vision brain must SEE the final screen: things like a playing video
        # barely show up in the UIA text menu, and a text-only verdict wrongly
        # rejects real successes (which then makes the agent redo/undo the task).
        image = None
        if self.cfg.brain.use_vision:
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
        if not isinstance(obj, dict) or "success" not in obj:
            return None, "unparseable verdict"
        return bool(obj.get("success")), str(obj.get("reason", ""))[:200]

    @staticmethod
    def _memory_has(task: str, memory: str) -> bool:
        """True when a stored '- Learned Task:' TITLE matches ``task`` (same
        fuzzy both-ways rule as _evict_memory). Matching only titles fixes a
        bug where a task that appeared as a substring anywhere in the memory
        prose (inside some plan's description) wrongly triggered plan reuse."""
        want = (task or "").strip().lower()
        if not want or not memory:
            return False
        marker = "- learned task:"
        for line in memory.lower().splitlines():
            line = line.strip()
            if line.startswith(marker):
                stored = line[len(marker):].strip()
                if stored and (want in stored or stored in want):
                    return True
        return False

    def _read_memory(self) -> str:
        try:
            return (self.memory_path.read_text(encoding="utf-8")
                    if self.memory_path.exists() else "")
        except Exception as exc:
            log.warn(f"Failed to read memory file: {exc}")
            return ""

    def _evict_memory(self, task: str) -> None:
        """Drop a learned plan entry that no longer works."""
        from .memory import evict_learned_plan
        evict_learned_plan(self.memory_path, task)

    def _append_memory(self, task: str, plan: dict) -> None:
        """Persist a verified-successful plan, deduped and size-capped."""
        from .memory import append_learned_plan
        append_learned_plan(self.memory_path, task, plan, self.cfg.data.memory_max_chars)

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
            "screen, anything on their machine). If unsure, choose task.\n"
            "If the user asks you to remember something or provides a fact/preference to keep, "
            'include "remember": "<fact to store forever>" in your response JSON.\n'
            "Reply with ONE JSON object and nothing else:\n"
            '  {"mode":"chat","reply":"<friendly reply>","remember":"<optional fact to store forever>"}\n'
            '   or   {"mode":"task"}'
            + remote_note
        )
        messages: list[dict] = []
        if chat_note:
            messages.append({"role": "user", "content": "Recent conversation:" + chat_note})
        messages.append({"role": "user", "content": task})
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

    def _maybe_image(self, obs, step_i: int):
        """Capture (and optionally annotate) a screenshot when needed."""
        if not (self.cfg.brain.use_vision or self.cfg.perception.save_screenshots):
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
        return shot.image if self.cfg.brain.use_vision else None

    def _with_observation(self, messages: list[dict], obs) -> list[dict]:
        """Append the current screen state to the latest user turn."""
        state = format_observation(obs.active_window, obs.screen_size, obs.menu())
        out = list(messages)
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
            return True
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
