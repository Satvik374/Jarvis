"""Dual-Agent Orchestrator: Connecting the Front Communicating Agent to the Silent Main Worker Agent.

Architecture:
  1. Main Worker Agent (Silent Engine):
     - Executes all desktop, browser, coding, OS, and tool tasks.
     - Operates silently in the background (no spoken monologues/summaries).
     - Emits high-frequency telemetry events (plans, steps, actions, reasoning, results).

  2. Side / Communicating Agent (Front Face & Voice):
     - First point of contact for the user (Voice & Chat).
     - Speculative Fast Fillers: Acknowledges tasks instantly (<20ms) with natural conversational fillers.
     - Live Supervision & Telemetry: Monitors Main Agent in real-time.
     - Concurrent Q&A: Answers user questions about what the Main Agent is doing right now,
       or converses naturally about unrelated topics without interrupting the background worker.
     - Unrestricted Interruption (Barge-in): Interruptible at any millisecond in voice or chat.
"""

from __future__ import annotations

import collections
import io
import queue
import sys
import threading
import time
from typing import Any

from ..agent.brain import make_brain, BrainError
from ..agent.loop import Agent
from ..config import Config
from ..utils import logging as log
from ..utils import voice
from .audio import LiveAudioStream
from .client import GeminiLiveClient
from .speculative import FastFillerEngine, get_fast_filler
from .telemetry_state import TaskTelemetryTracker


class LiveVoiceSupervisor:
    """Supervises real-time voice interactions, task delegation, and mid-task narration."""

    def __init__(self, cfg: Config, agent: Agent | None = None):
        self.cfg = cfg
        if agent is None:
            brain = make_brain(cfg.brain)
            self.agent = Agent(brain, cfg)
        else:
            self.agent = agent

        # Configure voice subsystem
        voice.configure(self.agent.brain, self.cfg.voice)

        # Speculative Fast Filler Engine & Live Telemetry State
        self.filler_engine = FastFillerEngine()
        self.tracker = TaskTelemetryTracker(max_history=25)

        # Audio stream
        self.audio_stream = LiveAudioStream(
            rate_in=self.cfg.live_voice.sample_rate_in,
            rate_out=self.cfg.live_voice.sample_rate_out,
            on_audio_in=self._on_mic_audio,
            on_barge_in=self._on_barge_in,
            barge_in_sensitivity=self.cfg.live_voice.barge_in_sensitivity,
        )

        # Gemini Live client
        self.client = GeminiLiveClient(
            config=self.cfg.live_voice,
            on_audio_out=self._on_live_audio_out,
            on_text=self._on_live_text_out,
            on_tool_call=self._on_live_tool_call,
            on_interrupted=self._on_live_interrupted,
        )

        # Task worker state
        self._active_task_thread: threading.Thread | None = None
        self._active_task_cancel = threading.Event()
        self._current_task = ""
        self._current_step = 0
        self._total_steps = self.cfg.safety.max_steps
        self._current_plan_name = ""
        self._is_task_running = False
        self._task_lock = threading.Lock()
        # Task launches can arrive concurrently from a live tool call and the
        # terminal.  Serialise replacement without holding _task_lock while a
        # previous worker joins (that worker needs _task_lock in its finally).
        self._launch_lock = threading.Lock()
        self._task_generation = 0

        # Mid-task interactive question handling
        self._pending_question = ""
        self._question_answer_queue: queue.Queue[str] = queue.Queue()
        self._waiting_for_answer = False

        # Narration throttle & state
        self._last_narration_time = 0.0
        self._min_narration_interval = 3.5  # seconds between mid-task voice updates
        self._narration_history: collections.deque[str] = collections.deque(maxlen=15)

        # Screen sharing. The live model asks to look (`share_screen`); from then
        # until `stop_screen_share` the supervisor grabs the desktop about once a
        # second and streams the frames through the session it already has. A
        # single still frame is not perceived at all, so this is a stream.
        self._screen_share_stop = threading.Event()
        self._screen_share_thread: threading.Thread | None = None
        self._screen_share_reason = ""
        self._screen_share_frames = 0

        self._is_running = False

    @property
    def is_task_running(self) -> bool:
        return self._is_task_running

    @property
    def is_sharing_screen(self) -> bool:
        thread = self._screen_share_thread
        return bool(thread is not None and thread.is_alive())

    @property
    def current_task(self) -> str:
        return self._current_task

    def start(self) -> bool:
        """Start the live voice supervisor system."""
        self._is_running = True
        log.ok("Starting Dual-Agent Live Voice Supervisor...")

        # 1. Start audio hardware stream
        audio_ok = self.audio_stream.start()
        if not audio_ok:
            log.warn("Microphone stream unavailable; running in text-assisted live mode.")

        # 2. Start WebSocket client
        ws_ok = self.client.start()

        return True

    def stop(self) -> None:
        """Stop all live streams and abort active task."""
        self._is_running = False
        # Before anything is torn down: screen frames are expensive to produce
        # and must not outlive the session that carries them.
        self.stop_screen_share()
        self.cancel_active_task()
        with self._task_lock:
            worker = self._active_task_thread
        if worker is not None and worker.is_alive():
            worker.join(timeout=1.5)
        self.client.stop()
        self.audio_stream.stop()
        voice.interrupt_speech()

    def launch_task(self, task: str) -> dict[str, Any]:
        """Dispatch a user command to the Main Worker Agent with instant speculative fast-filler."""
        task = (task or "").strip()
        if not task:
            return {"status": "error", "message": "Empty task description"}

        # Casual conversation check: greetings, small-talk, and general questions
        # should be answered directly and naturally in voice without invoking computer automation.
        if hasattr(self.agent, "_looks_like_task") and not self.agent._looks_like_task(task):
            from ..console import _handle_idle_conversation
            chat_reply = _handle_idle_conversation(task, self.agent)
            if chat_reply:
                log.jarvis(f"🎙️ [Communicating Agent]: {chat_reply}")
                self._narrate_voice(chat_reply, force=True)
                return {"status": "chat", "reply": chat_reply}

        # 1. Speculative Fast Filler (Instant Acknowledgment with 0ms perceived delay)
        fast_filler = self.filler_engine.get_fast_filler(task)
        if fast_filler:
            self._narrate_voice(fast_filler, force=True)

        # Never run two Agent.run calls against the same desktop/agent at once.
        # A shared cancellation Event previously allowed an older worker to be
        # revived when the next task cleared it, and its final callback could
        # then overwrite the new task's progress.
        with self._launch_lock:
            with self._task_lock:
                previous_worker = self._active_task_thread

            if previous_worker is not None and previous_worker.is_alive():
                log.info(f"Superseding previous task with new task: '{task}'")
                self._cancel_current_task(narrate=False)
                previous_worker.join(timeout=1.5)
                if previous_worker.is_alive():
                    message = "The current task is still stopping. Please try the new task again in a moment."
                    self._narrate_voice(message, force=True)
                    return {"status": "cancelling", "task": task, "message": message}

            with self._task_lock:
                self._task_generation += 1
                generation = self._task_generation
                cancel_event = threading.Event()
                self._active_task_cancel = cancel_event
                self._current_task = task
                self._current_step = 0
                self._current_plan_name = "Initializing"
                self._is_task_running = True
                self._waiting_for_answer = False
                self._pending_question = ""
                self._drain_question_answers()

                # Reset telemetry tracker for new task
                self.tracker.reset_for_new_task(task, max_steps=self.cfg.safety.max_steps)

                # Start silent Main Worker Agent in background thread
                self._active_task_thread = threading.Thread(
                    target=self._run_agent_task_worker,
                    args=(task, generation, cancel_event),
                    daemon=True,
                    name="jarvis-main-worker",
                )
                self._active_task_thread.start()

                return {
                    "status": "started",
                    "task": task,
                    "message": f"Task launched. Main Worker Agent executing: {task}",
                }

    def send_user_message(self, text: str) -> None:
        """Route user speech/text turn directly to the Communicating Agent with live worker context."""
        text = (text or "").strip()
        if not text:
            return

        # Prepare contextual payload including live Main Agent status if active
        live_context = ""
        if self._is_task_running:
            live_context = f"\n\n{self.tracker.format_live_context_for_prompt()}"

        message_payload = f"{text}{live_context}" if live_context else text

        for _ in range(15):
            if self.client.is_connected:
                self.client.send_text_turn(message_payload)
                return
            time.sleep(0.1)

        # Do not turn a status question into a replacement task when the live
        # websocket is reconnecting.  The previous behaviour cancelled the
        # active worker simply because the user asked how it was going.
        if self.is_task_running:
            summary = self.tracker.get_status_summary()
            response = (
                f"The Main Worker is {summary['status']} on '{summary['active_task']}'. "
                f"It is at step {summary['current_step']} of {summary['total_steps']}."
            )
            self._narrate_voice(response, force=True)
            return

        # If websocket is offline and no task is active, the main Agent still
        # provides a useful text-assisted fallback for the user.
        if hasattr(self.agent, "_looks_like_task") and not self.agent._looks_like_task(text):
            from ..console import _handle_idle_conversation
            chat_reply = _handle_idle_conversation(text, self.agent)
            if chat_reply:
                log.jarvis(f"🎙️ [Communicating Agent]: {chat_reply}")
                self._narrate_voice(chat_reply, force=True)
                return

        self.launch_task(text)

    def cancel_active_task(self) -> dict[str, Any]:
        """Cancel current Main Worker Agent execution."""
        if not self._cancel_current_task(narrate=True):
            return {"status": "idle", "message": "No active task running."}
        return {"status": "cancelled", "message": "Task cancelled by user."}

    def _cancel_current_task(self, narrate: bool) -> bool:
        """Signal the active worker without waiting while holding state locks."""
        with self._task_lock:
            if not self._is_task_running:
                return False

            self._active_task_cancel.set()
            self.agent.cancel()
            self._is_task_running = False
            self._waiting_for_answer = False
            self._pending_question = ""
            # Unblock an Agent waiting for an answer.  Its cancellation event
            # remains set, so it cannot report the question as a completion.
            try:
                self._question_answer_queue.put_nowait("")
            except queue.Full:
                pass
            self.tracker.update_event({"event": "cancelled"})
            log.warn(f"Task '{self._current_task}' cancelled by user.")
        if narrate:
            self._narrate_voice("Task cancelled, sir.", force=True)
        return True

    def answer_question(self, answer: str) -> None:
        """Provide user answer to a mid-task agent question."""
        if self._waiting_for_answer:
            self._question_answer_queue.put(answer)
            self._waiting_for_answer = False

    def _drain_question_answers(self) -> None:
        """Discard answers left behind by a cancelled or superseded task."""
        while True:
            try:
                self._question_answer_queue.get_nowait()
            except queue.Empty:
                return

    def _is_current_run(self, generation: int, cancel_event: threading.Event) -> bool:
        with self._task_lock:
            return (
                generation == self._task_generation
                and cancel_event is self._active_task_cancel
            )

    def _run_agent_task_worker(
        self,
        task: str,
        generation: int,
        cancel_event: threading.Event,
    ) -> None:
        """Background thread executing the silent Main Worker Agent."""
        log.step(f"⚡ [Main Worker Agent] Executing task: {task}")
        try:
            # Main Agent runs silently: no direct TTS output
            result = self.agent.run(
                task=task,
                asker=lambda question: self._ask_user_bridge(question, generation, cancel_event),
                cancel_event=cancel_event,
                on_progress=lambda event: self._on_agent_progress_event(event, generation, cancel_event),
            )
            if cancel_event.is_set():
                if self._is_current_run(generation, cancel_event):
                    self.tracker.update_event({"event": "cancelled"})
            else:
                log.ok(f"⚡ [Main Worker Agent] Task completed: {result}")
                self._on_task_completed(result, success=True, generation=generation,
                                        cancel_event=cancel_event)
        except Exception as exc:
            log.error(f"⚡ [Main Worker Agent] Task failed: {exc}")
            if not cancel_event.is_set():
                self._on_task_completed(str(exc), success=False, generation=generation,
                                        cancel_event=cancel_event)
        finally:
            with self._task_lock:
                if generation == self._task_generation and cancel_event is self._active_task_cancel:
                    self._is_task_running = False
                    self._waiting_for_answer = False

    def _on_agent_progress_event(
        self,
        event: dict[str, Any],
        generation: int | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        """Telemetry handler monitoring Main Worker Agent steps in real time."""
        if generation is not None and cancel_event is not None:
            if not self._is_current_run(generation, cancel_event):
                return
            # An Agent waiting on a user question can emit its final event as
            # the cancellation unblocks it. Do not let that late event replace
            # the already-visible cancelled state.
            if cancel_event.is_set() and event.get("event") != "cancelled":
                return
        # 1. Update centralized thread-safe state tracker
        self.tracker.update_event(event)

        ev_type = event.get("event", "")

        if ev_type == "plan_start":
            self._current_plan_name = event.get("plan_name", "Executing")
            desc = event.get("plan_description", "")
            log.info(f"⚡ [Telemetry] Plan: {self._current_plan_name}")
            if self.cfg.live_voice.narrate_steps:
                narration = f"Plan initiated: {self._current_plan_name}."
                if desc:
                    narration += f" {desc[:90]}."
                self._narrate_voice(narration)

        elif ev_type == "step_action":
            self._current_step = event.get("step", self._current_step + 1)
            thought = (event.get("thought") or "").strip()
            action = event.get("action", "")
            args = event.get("args", {})
            log.info(f"⚡ [Telemetry] Step {self._current_step}: {action}")

            if self.cfg.live_voice.narrate_steps:
                self._generate_mid_task_narration(
                    step=self._current_step,
                    thought=thought,
                    action=action,
                    args=args,
                )

        elif ev_type == "step_result":
            result_msg = event.get("result", "")
            is_ok = event.get("ok", True)
            log.debug(f"⚡ [Telemetry] Step {self._current_step} result ({is_ok}): {result_msg[:100]}")

        elif ev_type == "ask":
            question = event.get("question", "")
            self._pending_question = question
            self._waiting_for_answer = True
            log.info(f"⚡ [Telemetry] Mid-task question: {question}")
            # The Agent invokes _ask_user_bridge immediately after this event.
            # Let that bridge speak once rather than asking the user twice.

        elif ev_type == "cancelled":
            log.warn("⚡ [Telemetry] Task cancelled signal received.")

    def _generate_mid_task_narration(
        self,
        step: int,
        thought: str,
        action: str,
        args: dict[str, Any],
    ) -> None:
        """Formulate and deliver natural mid-task voice narration."""
        now = time.time()
        if now - self._last_narration_time < self._min_narration_interval:
            return  # Throttle to avoid audio clutter

        narration = self._build_narration_sentence(step, thought, action, args)
        if narration:
            self._narrate_voice(narration)

    def _build_narration_sentence(
        self,
        step: int,
        thought: str,
        action: str,
        args: dict[str, Any],
    ) -> str:
        """Craft a natural executive phrase describing what is done & what will be done next."""
        act_desc = ""
        next_desc = ""

        if action == "click":
            elem_id = args.get("element") or args.get("id")
            act_desc = f"clicking element {elem_id}" if elem_id is not None else "clicking target"
        elif action in {"type", "type_text"}:
            act_desc = "typing input"
        elif action == "press":
            act_desc = f"pressing {args.get('key', 'key')}"
        elif action in {"launch", "open_app"}:
            app = args.get("app") or args.get("name") or "application"
            act_desc = f"launching {app}"
            next_desc = f"I'll prepare the workspace once {app} is ready."
        elif action in {"python", "run_command"}:
            act_desc = "running script in terminal"
        elif action == "read_file":
            path = args.get("path", "file")
            act_desc = f"reading {path}"
        elif action in {"write_file", "edit_file"}:
            path = args.get("path", "file")
            act_desc = f"updating {path}"
        elif action in {"web_search", "read_url"}:
            act_desc = "fetching web data"
        elif action == "synthesize_tool":
            tool_name = args.get("name", "custom tool")
            act_desc = f"synthesizing dynamic tool '{tool_name}'"
        elif action == "execute_synthesized_tool":
            tool_name = args.get("name", "tool")
            act_desc = f"executing synthesized tool '{tool_name}'"
        else:
            act_desc = f"executing {action}"

        if thought and len(thought) > 10:
            cleaned_thought = thought.split(".")[0].strip()
            if len(cleaned_thought) < 80:
                return f"Currently {act_desc}. {cleaned_thought}."

        if next_desc:
            return f"Step {step}: Now {act_desc}. {next_desc}"
        return f"Step {step}: Now {act_desc}."

    def _narrate_voice(self, message: str, force: bool = False) -> None:
        """Deliver narration to user via Gemini Live session or TTS engine."""
        message = (message or "").strip()
        if not message:
            return

        now = time.time()
        if not force and now - self._last_narration_time < self._min_narration_interval:
            return

        self._last_narration_time = now
        self._narration_history.append(message)
        log.jarvis(f"🎙️ [Communicating Agent]: {message}")

        if self.client.is_connected:
            prompt_context = (
                f"[SYSTEM NOTIFICATION for Jarvis Communicating Agent: "
                f"Inform user aloud: '{message}']"
            )
            self.client.send_text_turn(prompt_context)
        else:
            voice.speak(message, wait=False)

    def _on_task_completed(
        self,
        summary: str,
        success: bool = True,
        generation: int | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        """Notify user when task has concluded."""
        if generation is not None and cancel_event is not None:
            if not self._is_current_run(generation, cancel_event) or cancel_event.is_set():
                return
        self.tracker.update_event({"event": "finish" if success else "error", "result": summary})
        clean_summary = summary.replace("\n", " ").strip()[:160]
        is_task = (
            hasattr(self.agent, "_looks_like_task")
            and self.agent._looks_like_task(getattr(self, "_current_task", ""))
        )
        if is_task:
            prefix = "Completed: " if success else "Note on task: "
            final_msg = f"{prefix}{clean_summary}"
        else:
            final_msg = clean_summary
        self._narrate_voice(final_msg, force=True)

    def _ask_user_bridge(
        self,
        question: str,
        generation: int | None = None,
        cancel_event: threading.Event | None = None,
    ) -> str:
        """Bridges agent mid-task questions into live voice interaction."""
        if generation is not None and cancel_event is not None:
            if not self._is_current_run(generation, cancel_event) or cancel_event.is_set():
                return ""
        self._pending_question = question
        self._waiting_for_answer = True
        self._narrate_voice(f"Excuse me sir, {question}", force=True)

        try:
            answer = self._question_answer_queue.get(timeout=60.0)
            return answer
        except queue.Empty:
            log.warn("Mid-task question timed out without user voice response.")
            return ""
        finally:
            self._waiting_for_answer = False
            self._pending_question = ""

    # ------------------------------------------------------------------ #
    # Audio & Live Client Callbacks
    # ------------------------------------------------------------------ #
    def _on_mic_audio(self, pcm_chunk: bytes) -> None:
        """Microphone capture callback."""
        if self.client.is_connected:
            self.client.send_audio(pcm_chunk)

    def _on_live_audio_out(self, pcm_chunk: bytes) -> None:
        """Speaker playback callback from Gemini Live API."""
        self.audio_stream.play_chunk(pcm_chunk)

    def _on_live_text_out(self, text: str) -> None:
        """Transcript chunk from Gemini Live API."""
        if text:
            sys.stdout.write(f"\033[93m{text}\033[0m")
            sys.stdout.flush()

    def _on_live_tool_call(self, name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
        """Tool call handler invoked by the Communicating Agent."""
        args = args if isinstance(args, dict) else {}
        log.info(f"🎙️ [Communicating Agent Tool Call]: {name}({args})")

        if name == "share_screen":
            return self.start_screen_share(str(args.get("reason", "") or ""))

        elif name == "stop_screen_share":
            return self.stop_screen_share()

        elif name == "run_jarvis_task":
            task = args.get("task", "")
            return self.launch_task(task)

        elif name == "cancel_task":
            return self.cancel_active_task()

        elif name in {"ask_task_status", "get_task_status"}:
            summary = self.tracker.get_status_summary()
            summary["running"] = self._is_task_running
            summary["task"] = self._current_task
            summary["step"] = self._current_step
            summary["plan"] = self._current_plan_name
            summary["recent_narration"] = list(self._narration_history)[-3:]
            return summary

        elif name == "answer_agent_question":
            ans = str(args.get("answer", "")).strip()
            if not ans:
                return {"status": "error", "message": "An answer is required."}
            if not self._waiting_for_answer:
                return {"status": "idle", "message": "The Main Worker is not awaiting an answer."}
            self.answer_question(ans)
            return {"status": "answered", "answer": ans}

        return {"status": "unknown_tool", "tool": name}

    # ------------------------------------------------------------------ #
    # Screen sharing
    # ------------------------------------------------------------------ #

    def start_screen_share(self, reason: str = "") -> dict[str, Any]:
        """Stream the desktop to the live model until it stops asking.

        Returns a tool response the model speaks from, so a refused or already
        running share is something it can say out loud instead of assuming it is
        looking at a screen it never received.
        """
        if not getattr(self.cfg.live_voice, "screen_share", True):
            return {
                "status": "disabled",
                "message": "Screen sharing is switched off (live_voice.screen_share).",
            }
        if self.is_sharing_screen:
            return {
                "status": "already_sharing",
                "reason": self._screen_share_reason,
                "frames": self._screen_share_frames,
            }

        interval = float(getattr(self.cfg.live_voice, "screen_share_interval", 1.0) or 1.0)
        self._screen_share_reason = reason
        self._screen_share_frames = 0
        self._screen_share_stop.clear()
        self._screen_share_thread = threading.Thread(
            target=self._stream_screen_frames,
            name="live-screen-share",
            daemon=True,
        )
        self._screen_share_thread.start()
        log.info(f"🖥️ [Screen Share] Streaming the desktop to the Communicating Agent ({interval:.1f}s/frame).")
        return {
            "status": "sharing",
            "reason": reason,
            "interval_seconds": interval,
            "note": (
                "Frames start arriving now and continue about once a second. Say what you "
                "see from the frames themselves, and call stop_screen_share when done."
            ),
        }

    def stop_screen_share(self) -> dict[str, Any]:
        """End the frame stream. Safe to call when nothing is being shared."""
        was_sharing = self.is_sharing_screen
        self._screen_share_stop.set()
        thread = self._screen_share_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        frames = self._screen_share_frames
        self._screen_share_thread = None
        if was_sharing:
            log.info(f"🖥️ [Screen Share] Stopped after {frames} frame(s).")
        return {"status": "stopped", "frames": frames}

    def _stream_screen_frames(self) -> None:
        """Capture, downscale and send one frame per interval until stopped."""
        interval = max(0.25, float(getattr(self.cfg.live_voice, "screen_share_interval", 1.0) or 1.0))
        max_dim = int(getattr(self.cfg.live_voice, "screen_share_max_dim", 1024) or 1024)
        quality = int(getattr(self.cfg.live_voice, "screen_share_quality", 60) or 60)
        try:
            from ..perception.live_vision import get_live_vision

            vision = get_live_vision()
        except Exception as exc:  # pragma: no cover - capture stack unavailable
            log.warn(f"[Screen Share] Screen capture is unavailable: {exc}")
            return

        while not self._screen_share_stop.is_set():
            if not self.client.is_connected:
                log.warn("[Screen Share] The live session ended; stopping the frame stream.")
                break
            try:
                image = vision.capture_screen()
                # Downscale before encoding: a full-resolution desktop is sent
                # about once a second and would otherwise spend the session's
                # bandwidth on detail no screen check needs.
                image.thumbnail((max_dim, max_dim))
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=quality)
                self.client.send_video_frame(buffer.getvalue())
                self._screen_share_frames += 1
            except Exception as exc:
                log.warn(f"[Screen Share] Dropped a frame: {exc}")
            self._screen_share_stop.wait(interval)

        self._screen_share_thread = None

    def _on_live_interrupted(self) -> None:
        """User spoke while Communicating Agent was speaking (Barge-in)."""
        log.info("⚡ [Dual-Agent] User barge-in detected; silencing audio playback.")
        self.audio_stream.clear_playback()
        voice.interrupt_speech()

    def _on_barge_in(self) -> None:
        """Audio hardware VAD detected user speech during playback."""
        self._on_live_interrupted()


def run_live_mode(cfg: Config) -> int:
    """Run interactive Dual-Agent Live Voice Supervisor session in terminal."""
    from .cli import LiveCliHUD

    supervisor = LiveVoiceSupervisor(cfg)
    hud = LiveCliHUD(supervisor)
    return hud.run()
