"""Unit tests for Jarvis Stop / Cancellation functionality across Agent, HUD, and Macros."""

import threading
import time
import unittest
from unittest.mock import Mock, patch

from jarvis.config import Config
from jarvis.agent.loop import Agent, cancel_active_agent, get_active_agent
from jarvis.hud.controller import HudController
from jarvis.macro.manager import Macro, MacroStep, MacroManager
from jarvis.macro.player import MacroPlayer


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

    def test_03_agent_cancellation_during_fast_path_macro(self):
        """Test that fast-path macro playback aborts immediately when cancel_event is set."""
        brain = Mock()
        agent = Agent(brain, self.cfg)

        macro_mgr = Mock(spec=MacroManager)
        macro = Macro(
            name="long_macro",
            description="open app and perform macro",
            steps=[MacroStep(action="wait", args={"seconds": 5.0}) for _ in range(10)]
        )
        macro_mgr.find_matching_macro.return_value = (macro, {}, 0.99)
        agent.macro_mgr = macro_mgr

        player = Mock(spec=MacroPlayer)

        def mock_play(m, speed=1.0, params=None, cancel_event=None):
            if cancel_event:
                cancel_event.set()
            return {"ok": False, "message": "Playback interrupted by user cancel.", "steps_executed": 1}

        player.play = mock_play
        agent.macro_player = player

        res = agent.run("open app and perform macro")


        self.assertIn("cancelled", res.lower())

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


if __name__ == "__main__":
    unittest.main()
