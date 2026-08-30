"""Unit tests for Self-Compiling Procedural Memory & Fast-Path Macro Replay."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from jarvis.agent.loop import Agent
from jarvis.agent.trajectory import Step, Trajectory
from jarvis.config import Config
from jarvis.macro import (
    Macro,
    MacroManager,
    MacroPlayer,
    MacroStep,
    TrajectoryCompiler,
    get_macro_manager,
    get_trajectory_compiler,
)


class ProceduralFastPathTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_dir = Path(self.temp_dir.name) / "macros"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.macro_mgr = get_macro_manager(storage_dir=self.storage_dir)
        self.compiler = get_trajectory_compiler(macro_manager=self.macro_mgr)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_01_trajectory_compilation_and_pruning(self):
        # Create a sample trajectory with:
        # - 1 open_app step
        # - 1 failed exploratory step (should be pruned)
        # - 1 click with element id resolved to coordinates
        # - 2 consecutive wait steps (should be merged)
        # - 1 type step with quoted text from prompt
        traj = Trajectory(
            task='open notepad and type "SuperSecretPassword123"',
            backend="ollama",
            model="ornith:9b",
        )
        traj.add(
            Step(
                active_window="Desktop",
                elements=[],
                menu="",
                thought="Launch notepad",
                action="open_app",
                args={"app": "notepad"},
                result="Launched notepad",
                ok=True,
            )
        )
        # Failed exploratory step:
        traj.add(
            Step(
                active_window="Notepad",
                elements=[],
                menu="",
                thought="Wrong button click",
                action="click",
                args={"x": 10, "y": 10},
                result="Clicked outside",
                ok=False,
            )
        )
        # Valid element click:
        traj.add(
            Step(
                active_window="Untitled - Notepad",
                elements=[
                    {
                        "id": 5,
                        "name": "Text Editor",
                        "control_type": "Document",
                        "center": (350, 420),
                    }
                ],
                menu="[5] Document 'Text Editor' @ (350, 420)",
                thought="Click text area",
                action="click",
                args={"element": 5},
                result="Clicked editor",
                ok=True,
            )
        )
        # Consecutive waits:
        traj.add(
            Step(
                active_window="Untitled - Notepad",
                elements=[],
                menu="",
                thought="Wait",
                action="wait",
                args={"seconds": 0.5},
                result="waited",
                ok=True,
            )
        )
        traj.add(
            Step(
                active_window="Untitled - Notepad",
                elements=[],
                menu="",
                thought="Wait more",
                action="wait",
                args={"seconds": 0.5},
                result="waited",
                ok=True,
            )
        )
        # Type step:
        traj.add(
            Step(
                active_window="Untitled - Notepad",
                elements=[],
                menu="",
                thought="Type the text",
                action="type",
                args={"text": "SuperSecretPassword123"},
                result="typed text",
                ok=True,
            )
        )
        # Finish step (should be ignored):
        traj.add(
            Step(
                active_window="Untitled - Notepad",
                elements=[],
                menu="",
                thought="Done",
                action="finish",
                args={"summary": "task complete"},
                result="done",
                ok=True,
            )
        )

        compiled_macro = self.compiler.compile(traj)
        self.assertIsNotNone(compiled_macro)
        self.assertIn("Notepad", compiled_macro.target_apps)

        # Check that the failed step and finish step were pruned
        actions = [s.action for s in compiled_macro.steps]
        self.assertEqual(actions, ["launch", "click", "wait", "type"])

        # Check coordinate resolution for element 5
        click_step = compiled_macro.steps[1]
        self.assertEqual(click_step.args.get("x"), 350)
        self.assertEqual(click_step.args.get("y"), 420)

        # Check wait merging: 0.5 + 0.5 = 1.0s
        wait_step = compiled_macro.steps[2]
        self.assertEqual(wait_step.args.get("seconds"), 1.0)

        # Check parameter slot extraction: "SuperSecretPassword123" replaced with {text}
        type_step = compiled_macro.steps[3]
        self.assertEqual(type_step.args.get("text"), "{text}")
        self.assertIn("text", compiled_macro.parameters)

    def test_02_macro_manager_fast_matching_and_param_extraction(self):
        # Save a parameterized macro
        macro = Macro(
            name="open_notepad_and_write",
            description="open notepad and type {text}",
            steps=[
                MacroStep(action="launch", args={"command": "notepad"}),
                MacroStep(action="type", args={"text": "{text}"}),
            ],
            parameters=["text"],
            target_apps=["Notepad"],
        )
        self.macro_mgr.save_macro(macro, sync_memory=False)

        # 1. Matching exact slug/name
        m, params, conf = self.macro_mgr.find_matching_macro("open notepad and type 'Iron Man Armor Specs'")
        self.assertIsNotNone(m)
        self.assertEqual(m.name, "open_notepad_and_write")
        self.assertGreaterEqual(conf, 0.85)
        self.assertEqual(params.get("text"), "Iron Man Armor Specs")

    def test_03_fast_path_instant_execution_in_agent_run(self):
        # Create an Agent with a mock brain
        brain = Mock()
        cfg = Config()
        cfg.data.collect_trajectories = False
        cfg.data.trajectory_dir = self.temp_dir.name
        agent = Agent(brain, cfg)
        agent.macro_mgr = self.macro_mgr
        agent.macro_player = MacroPlayer(self.macro_mgr)

        # Pre-populate a macro
        macro = Macro(
            name="quick_search",
            description="open browser and search {query}",
            steps=[
                MacroStep(action="type", args={"text": "{query}"}),
            ],
            parameters=["query"],
        )
        self.macro_mgr.save_macro(macro, sync_memory=False)

        # Mock player.play to simulate fast execution
        executed_params = {}

        def mock_play(m, speed=1.0, params=None, cancel_event=None):
            nonlocal executed_params
            executed_params = params or {}
            return {"ok": True, "message": "Played successfully.", "steps_executed": 1}

        agent.macro_player.play = mock_play

        # Run task
        res = agent.run("open browser and search 'Quantum Physics'")

        # Assert zero LLM calls
        brain.complete.assert_not_called()
        self.assertIn("quantum physics", res.lower())
        self.assertTrue(res.startswith("I have"))
        self.assertEqual(executed_params.get("query"), "Quantum Physics")


    def test_04_fast_path_fallback_to_llm_on_playback_failure(self):
        brain = Mock()
        cfg = Config()
        cfg.data.collect_trajectories = False
        cfg.data.trajectory_dir = self.temp_dir.name
        agent = Agent(brain, cfg)
        agent.macro_mgr = self.macro_mgr
        agent.macro_player = MacroPlayer(self.macro_mgr)

        macro = Macro(
            name="flaky_macro",
            description="open calc and compute",
            steps=[MacroStep(action="click", args={"x": 50, "y": 50})],
        )
        self.macro_mgr.save_macro(macro, sync_memory=False)

        # Mock play to fail
        def mock_failing_play(m, speed=1.0, params=None, cancel_event=None):
            return {"ok": False, "message": "Target window not found", "failed_step_index": 1}

        agent.macro_player.play = mock_failing_play

        # Mock LLM complete to return a finish action on fallback
        brain.complete.return_value = '{"thought": "recovering via LLM", "action": "finish", "args": {"summary": "recovered"}}'

        with patch.object(agent, "_perceive", return_value=Mock(active_window="Desktop", screen_size=(1920, 1080), elements=[], menu=lambda: "")):
            with patch.object(agent, "_verify_success", return_value=(True, "ok")):
                res = agent.run("open calc and compute")

        # Assert that the brain was called on fallback
        brain.complete.assert_called()
        self.assertIn("recovered", res)

    def test_05_auto_compilation_and_subsequent_fastpath_run(self):
        # 1. First run: task completes via LLM with verified success
        brain = Mock()
        cfg = Config()
        cfg.data.collect_trajectories = False
        cfg.data.trajectory_dir = self.temp_dir.name
        agent = Agent(brain, cfg)
        agent.macro_mgr = self.macro_mgr
        agent.macro_player = MacroPlayer(self.macro_mgr)

        # Brain simulates 2 steps: launch notepad, then finish
        responses = [
            '{"thought": "launch notepad", "action": "open_app", "args": {"app": "notepad"}}',
            '{"thought": "done", "action": "finish", "args": {"summary": "opened notepad"}}',
        ]
        brain.complete.side_effect = responses

        obs_mock = Mock(active_window="Desktop", screen_size=(1920, 1080), elements=[], menu=lambda: "")
        with patch.object(agent, "_perceive", return_value=obs_mock):
            with patch.object(agent, "_verify_success", return_value=(True, "verified")):
                with patch("jarvis.tools.registry.execute", return_value=Mock(ok=True, message="ok", needs_observe=True, finished=False, ask=None, clear_image=False, image_path=None)) as mock_exec:
                    # When action is finish, return finished=True
                    def custom_exec(name, args, obs, cfg):
                        if name == "finish":
                            return Mock(ok=True, message="opened notepad", needs_observe=False, finished=True, ask=None, clear_image=False, image_path=None)
                        return Mock(ok=True, message="launched notepad", needs_observe=True, finished=False, ask=None, clear_image=False, image_path=None)

                    mock_exec.side_effect = custom_exec
                    res1 = agent.run("launch notepad application")

        self.assertIn("opened notepad", res1)

        # 2. Check that a macro was automatically compiled and saved
        macros = self.macro_mgr.list_macros()
        self.assertGreaterEqual(len(macros), 1)
        compiled = next((m for m in macros if "notepad" in m.name.lower()), None)
        self.assertIsNotNone(compiled)

        # 3. Second run: same task should now be intercepted on Fast-Path with 0 LLM calls!
        brain_second_run = Mock()
        agent_second_run = Agent(brain_second_run, cfg)
        agent_second_run.macro_mgr = self.macro_mgr
        agent_second_run.macro_player = MacroPlayer(self.macro_mgr)

        # Mock player.play to confirm it gets called directly
        played_macro_name = None

        def mock_play(m, speed=1.0, params=None, cancel_event=None):
            nonlocal played_macro_name
            played_macro_name = m.name if isinstance(m, Macro) else str(m)
            return {"ok": True, "message": "Fast-path executed.", "steps_executed": 1}

        agent_second_run.macro_player.play = mock_play

        res2 = agent_second_run.run("launch notepad application")

        # Assert zero LLM calls on second run!
        brain_second_run.complete.assert_not_called()
        self.assertTrue(res2.startswith("I have"))
        self.assertIn("notepad", res2.lower())
        self.assertIsNotNone(played_macro_name)



if __name__ == "__main__":
    unittest.main()
