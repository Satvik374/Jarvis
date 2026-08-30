"""Tests for Dual-Agent Architecture, Speculative Fast Fillers, and Live Telemetry."""

import io
import queue
import threading
import time
import unittest
from unittest.mock import Mock, patch

from jarvis.config import Config
from jarvis.console import (
    _cancel_console_worker,
    _format_side_agent_reply,
    _handle_idle_conversation,
    _handle_side_agent_chat,
)
from jarvis.live.speculative import FastFillerEngine, get_fast_filler
from jarvis.live.client import GeminiLiveClient
from jarvis.live.supervisor import LiveVoiceSupervisor
from jarvis.live.telemetry_state import TaskTelemetryTracker


class SpeculativeFastFillerTests(unittest.TestCase):
    def setUp(self):
        self.engine = FastFillerEngine()

    def test_intent_classification(self):
        self.assertEqual(self.engine.predict_intent("search for python tutorials online"), "browser_search")
        self.assertEqual(self.engine.predict_intent("write a python script to parse logs"), "coding")
        self.assertEqual(self.engine.predict_intent("open notepad and type hello"), "app_launch")
        self.assertEqual(self.engine.predict_intent("delete the old file in downloads"), "files")
        self.assertEqual(self.engine.predict_intent("check system cpu and memory status"), "system")
        self.assertEqual(self.engine.predict_intent("do some custom work"), "general_task")

    def test_fast_filler_latency_and_variety(self):
        t0 = time.perf_counter()
        filler1 = get_fast_filler("search for latest news")
        latency_ms = (time.perf_counter() - t0) * 1000
        self.assertLess(latency_ms, 5.0)  # sub-5ms latency
        self.assertTrue(len(filler1) > 10)

        # Ensure subsequent filler is valid
        filler2 = get_fast_filler("write a python script")
        self.assertTrue(len(filler2) > 10)


class TaskTelemetryTrackerTests(unittest.TestCase):
    def setUp(self):
        self.tracker = TaskTelemetryTracker(max_history=10)

    def test_lifecycle_and_events(self):
        self.tracker.reset_for_new_task("create snake game", max_steps=20)
        self.assertEqual(self.tracker.status, "running")
        self.assertEqual(self.tracker.active_task, "create snake game")

        # Plan event
        self.tracker.update_event({
            "event": "plan_start",
            "plan_name": "Direct Attempt",
            "plan_description": "Write HTML and JavaScript",
        })
        self.assertEqual(self.tracker.current_plan, "Direct Attempt")

        # Action event
        self.tracker.update_event({
            "event": "step_action",
            "step": 1,
            "max_steps": 20,
            "action": "write_file",
            "thought": "Writing HTML file",
            "args": {"path": "game.html"},
        })
        self.assertEqual(self.tracker.current_step, 1)
        self.assertEqual(self.tracker.current_action, "write_file")

        # Result event
        self.tracker.update_event({
            "event": "step_result",
            "step": 1,
            "action": "write_file",
            "result": "wrote 500 bytes",
            "ok": True,
        })
        self.assertEqual(len(self.tracker.step_history), 1)
        self.assertEqual(self.tracker.step_history[0].result, "wrote 500 bytes")

        summary = self.tracker.get_status_summary()
        self.assertEqual(summary["current_step"], 1)
        self.assertEqual(summary["current_action"], "write_file")
        self.assertIn("wrote 500 bytes", summary["recent_history"][0])

        context_str = self.tracker.format_live_context_for_prompt()
        self.assertIn("MAIN WORKER AGENT STATUS", context_str)
        self.assertIn("create snake game", context_str)

    def test_error_result_is_preserved(self):
        self.tracker.reset_for_new_task("test task")
        self.tracker.update_event({"event": "error", "result": "network timeout"})
        self.assertEqual(self.tracker.status, "error")
        self.assertEqual(self.tracker.last_result_summary, "network timeout")


class GeminiLiveClientConfigurationTests(unittest.TestCase):
    def test_configured_model_is_tried_before_fallbacks(self):
        cfg = Config().live_voice
        cfg.model = "models/custom-live-model"
        client = GeminiLiveClient(cfg)

        candidates = client._model_candidates(use_api_key=True)

        self.assertEqual(candidates[0], "models/custom-live-model")
        self.assertEqual(len(candidates), len(set(candidates)))


class ConsoleDualAgentDispatchTests(unittest.TestCase):
    def test_idle_greeting_stays_with_communicating_agent(self):
        agent = Mock()
        agent.cfg.brain.conversational = True
        agent._looks_like_task.return_value = False
        agent._chat_context.return_value = ""
        agent._maybe_chat.return_value = "Hello, sir. How can I help?"

        reply = _handle_idle_conversation("hey", agent)

        self.assertEqual(reply, "Hello, sir. How can I help?")
        agent._maybe_chat.assert_called_once_with("hey", "")
        agent._append_chat.assert_called_once_with("hey", reply)

    def test_desktop_task_bypasses_idle_chat(self):
        agent = Mock()
        agent.cfg.brain.conversational = True
        agent._looks_like_task.return_value = True

        reply = _handle_idle_conversation("open notepad", agent)

        self.assertIsNone(reply)
        agent._maybe_chat.assert_not_called()

    def test_side_agent_unwraps_response_json_as_markdown(self):
        raw = '''{
          "response": "To set a keyframe:\\n\\n1. Select the object.\\n2. Press **I**."
        }'''

        self.assertEqual(
            _format_side_agent_reply(raw),
            "To set a keyframe:\n\n1. Select the object.\n2. Press **I**.",
        )

    def test_side_agent_chat_requests_plain_markdown_and_formats_reply(self):
        tracker = Mock()
        tracker.get_status_summary.return_value = {"status": "running"}
        tracker.format_live_context_for_prompt.return_value = "worker context"
        brain = Mock()
        brain.complete.return_value = '{"response": "1. First\\n2. Second"}'

        reply = _handle_side_agent_chat("Explain keyframes", tracker, brain, Config())

        self.assertEqual(reply, "1. First\n2. Second")
        self.assertIn("Return plain Markdown only", brain.complete.call_args.args[0])

    def test_ctrl_c_cancels_worker_and_unblocks_question_wait(self):
        """Console cancellation must wake a worker blocked awaiting an answer."""
        worker = Mock()
        worker.is_alive.return_value = True
        cancel_event = threading.Event()
        done_event = threading.Event()
        answers: queue.Queue[str] = queue.Queue()
        waiting = [True]
        agent = Mock()
        tracker = Mock()

        with patch("jarvis.console.voice.interrupt_speech") as silence:
            cancelled = _cancel_console_worker(
                worker, cancel_event, done_event, answers, waiting, agent, tracker
            )

        self.assertTrue(cancelled)
        self.assertTrue(cancel_event.is_set())
        self.assertFalse(waiting[0])
        self.assertEqual(answers.get_nowait(), "")
        agent.cancel.assert_called_once_with()
        tracker.update_event.assert_called_once_with({"event": "cancelled"})
        silence.assert_called_once_with()

    def test_ctrl_c_does_not_cancel_finished_worker(self):
        worker = Mock()
        worker.is_alive.return_value = True
        cancel_event = threading.Event()
        done_event = threading.Event()
        done_event.set()
        answers: queue.Queue[str] = queue.Queue()
        waiting = [True]
        agent = Mock()
        tracker = Mock()

        with patch("jarvis.console.voice.interrupt_speech") as silence:
            cancelled = _cancel_console_worker(
                worker, cancel_event, done_event, answers, waiting, agent, tracker
            )

        self.assertFalse(cancelled)
        self.assertFalse(cancel_event.is_set())
        self.assertTrue(waiting[0])
        self.assertTrue(answers.empty())
        agent.cancel.assert_not_called()
        tracker.update_event.assert_not_called()
        silence.assert_not_called()


class DualAgentSupervisorTests(unittest.TestCase):
    def test_live_transcript_callback_is_safe(self):
        cfg = Config()
        supervisor = LiveVoiceSupervisor(cfg, agent=Mock())
        output = io.StringIO()

        with patch("jarvis.live.supervisor.sys.stdout", output):
            supervisor._on_live_text_out("Hello")

        self.assertIn("Hello", output.getvalue())

    def test_supervisor_fast_filler_dispatch(self):
        cfg = Config()
        mock_agent = Mock()
        mock_agent.run.return_value = "Task finished successfully"

        supervisor = LiveVoiceSupervisor(cfg, agent=mock_agent)
        supervisor.client._is_connected = False  # offline test mode

        narrated = []
        supervisor._narrate_voice = lambda msg, force=False: narrated.append(msg)

        # Launch task
        resp = supervisor.launch_task("search for quantum computing research")
        self.assertEqual(resp["status"], "started")
        self.assertTrue(len(narrated) >= 1)  # fast filler was triggered immediately
        self.assertEqual(supervisor.tracker.active_task, "search for quantum computing research")

        # Wait for mock worker thread to finish
        if supervisor._active_task_thread:
            supervisor._active_task_thread.join(timeout=2.0)

        self.assertEqual(supervisor.tracker.status, "completed")

    def test_supervisor_barge_in(self):
        cfg = Config()
        mock_agent = Mock()
        supervisor = LiveVoiceSupervisor(cfg, agent=mock_agent)

        silenced = []
        supervisor.audio_stream.clear_playback = lambda: silenced.append(True)

        supervisor._on_barge_in()
        self.assertTrue(len(silenced) >= 1)

    def test_cancellation_does_not_become_a_completed_task(self):
        cfg = Config()
        mock_agent = Mock()
        worker_started = threading.Event()

        def _run(**kwargs):
            worker_started.set()
            kwargs["cancel_event"].wait(timeout=1.0)
            return "Task cancelled by user."

        mock_agent.run.side_effect = _run
        supervisor = LiveVoiceSupervisor(cfg, agent=mock_agent)
        narrated: list[str] = []
        supervisor._narrate_voice = lambda msg, force=False: narrated.append(msg)

        self.assertEqual(supervisor.launch_task("long running task")["status"], "started")
        self.assertTrue(worker_started.wait(timeout=1.0))
        self.assertEqual(supervisor.cancel_active_task()["status"], "cancelled")
        supervisor._active_task_thread.join(timeout=2.0)

        self.assertEqual(supervisor.tracker.status, "cancelled")
        self.assertFalse(any(msg.startswith("Completed:") for msg in narrated))

    def test_replacement_waits_for_prior_worker_to_stop(self):
        cfg = Config()
        mock_agent = Mock()
        first_started = threading.Event()
        second_started = threading.Event()
        active_count = 0
        max_active = 0
        active_lock = threading.Lock()

        def _run(task, **kwargs):
            nonlocal active_count, max_active
            with active_lock:
                active_count += 1
                max_active = max(max_active, active_count)
            try:
                if task == "first task":
                    first_started.set()
                    kwargs["cancel_event"].wait(timeout=1.0)
                    return "first task cancelled"
                second_started.set()
                return "second task complete"
            finally:
                with active_lock:
                    active_count -= 1

        mock_agent.run.side_effect = _run
        supervisor = LiveVoiceSupervisor(cfg, agent=mock_agent)
        supervisor._narrate_voice = lambda *_args, **_kwargs: None

        supervisor.launch_task("first task")
        self.assertTrue(first_started.wait(timeout=1.0))
        self.assertEqual(supervisor.launch_task("second task")["status"], "started")
        self.assertTrue(second_started.wait(timeout=1.0))
        supervisor._active_task_thread.join(timeout=2.0)

        self.assertEqual(max_active, 1)
        self.assertEqual(supervisor.tracker.active_task, "second task")

    def test_offline_status_question_does_not_replace_active_task(self):
        cfg = Config()
        supervisor = LiveVoiceSupervisor(cfg, agent=Mock())
        supervisor._is_task_running = True
        supervisor._current_task = "index the documents"
        supervisor.tracker.reset_for_new_task("index the documents")
        supervisor._narrate_voice = lambda *_args, **_kwargs: None

        with patch.object(supervisor, "launch_task") as launch, \
             patch("jarvis.live.supervisor.time.sleep"):
            supervisor.send_user_message("What is the progress?")

        launch.assert_not_called()
