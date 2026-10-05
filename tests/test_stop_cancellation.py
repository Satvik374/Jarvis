"""Unit tests for Jarvis Stop / Cancellation functionality across Agent and HUD."""

import threading
import time
import unittest
from unittest.mock import Mock, patch

from jarvis.config import Config
from jarvis.agent.loop import Agent, cancel_active_agent, get_active_agent
from jarvis.hud.controller import HudController


class StopCancellationTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Config()

    def test_01_agent_cancellation_during_step_loop(self):
        """Test that calling cancel_active_agent() stops an active Agent.run immediately."""
        brain = Mock()
        step_count = 0

        def mock_complete(*args, **kwargs):
            nonlocal step_count
            step_count += 1
            # Simulate cancelling the agent on the first step
            if step_count == 1:
                cancel_active_agent()
            return '{"thought": "Working", "action": "wait", "args": {"seconds": 0.01}}'

        brain.complete.side_effect = mock_complete
        agent = Agent(brain, self.cfg)

        res = agent.run("Perform a long automation task")

        # The agent should stop after step 1 without running to max_steps
        self.assertIn("cancelled", res.lower())
        self.assertEqual(step_count, 1)

    def test_02_direct_cancel_method_on_agent_during_execution(self):
        """Test calling agent.cancel() while running aborts execution immediately."""
        brain = Mock()
        step_count = 0

        def mock_complete(*args, **kwargs):
            nonlocal step_count
            step_count += 1
            # Trigger agent.cancel()
            agent.cancel()
            return '{"thought": "Step 1", "action": "wait", "args": {"seconds": 0.01}}'

        brain.complete.side_effect = mock_complete
        agent = Agent(brain, self.cfg)

        res = agent.run("Some task")

        self.assertIn("cancelled", res.lower())
        self.assertEqual(step_count, 1)

    def test_04_hud_interrupt_stops_active_agent_and_silences_voice(self):
        """Test that HudController.interrupt() cancels the active agent and silences speech."""
        started_event = threading.Event()
        finish_event = threading.Event()

        def slow_task_runner(cmd):
            started_event.set()
            # Wait until interrupted or finished
            finish_event.wait(timeout=2.0)
            return "Completed"

        controller = HudController(cfg=self.cfg, task_runner=slow_task_runner)
        controller.start(start_overlay=False)

        # Start a task in background
        controller._on_user_submit("automate work")
        self.assertTrue(started_event.wait(timeout=1.0))
        self.assertEqual(controller.state, "thinking")

        # Press Stop (interrupt)
        controller.interrupt()

        self.assertEqual(controller.state, "idle")
        self.assertIn("Stopped", controller.detail_text)

        finish_event.set()
        controller.stop()

    def test_05_ctrl_c_in_char_input_raises_keyboard_interrupt(self):
        """Test that Ctrl+C character (\x03) raises KeyboardInterrupt in _char_input."""
        from jarvis.console import _char_input
        chars = ["\x03"]
        with self.assertRaises(KeyboardInterrupt):
            _char_input("> ", kbhit=lambda: bool(chars),
                        getwch=lambda: chars.pop(0),
                        grace=0.0, echo=lambda s: None,
                        menu=lambda *a: None)

    def test_06_cancel_console_worker_unblocks_waiting_states(self):
        """Test _cancel_console_worker cleanly signals cancel and unblocks worker."""
        from jarvis.console import _cancel_console_worker
        import queue

        worker = Mock(spec=threading.Thread)
        worker.is_alive.return_value = True
        cancel_event = threading.Event()
        done_event = threading.Event()
        answer_queue = queue.Queue()
        waiting_for_answer = [True]
        agent = Mock()
        tracker = Mock()

        cancelled = _cancel_console_worker(
            worker, cancel_event, done_event, answer_queue,
            waiting_for_answer, agent, tracker
        )

        self.assertTrue(cancelled)
        self.assertTrue(cancel_event.is_set())
        agent.cancel.assert_called_once()
        self.assertFalse(waiting_for_answer[0])
        self.assertEqual(answer_queue.get_nowait(), "")


if __name__ == "__main__":
    unittest.main()

