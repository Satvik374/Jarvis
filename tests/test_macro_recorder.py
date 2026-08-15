import tempfile
import unittest
from pathlib import Path

from jarvis.config import Config
from jarvis.macro import (
    Macro,
    MacroManager,
    MacroPlayer,
    MacroRecorder,
    MacroStep,
    get_macro_manager,
)
from jarvis.macro.recorder import RawEvent
from jarvis.tools import registry
from jarvis.tools.schema import ACTIONS_BY_NAME


class MacroRecorderTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.mgr = get_macro_manager(storage_dir=Path(self.temp_dir.name))
        self.player = MacroPlayer(macro_manager=self.mgr)
        self.recorder = MacroRecorder(macro_manager=self.mgr)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_01_macro_step_serialization(self):
        step = MacroStep(
            action="type",
            args={"text": "hello jarvis"},
            description="Type greeting",
            delay=0.3,
        )
        d = step.to_dict()
        self.assertEqual(d["action"], "type")
        self.assertEqual(d["args"]["text"], "hello jarvis")

        restored = MacroStep.from_dict(d)
        self.assertEqual(restored.action, "type")
        self.assertEqual(restored.args["text"], "hello jarvis")
        self.assertIn("Type \"hello jarvis\"", restored.summary())

    def test_02_macro_serialization_and_plan_format(self):
        steps = [
            MacroStep(action="focus_window", args={"title": "Notepad"}, description="Focus Notepad"),
            MacroStep(action="type", args={"text": "Hello World"}, description='Type "Hello World"'),
            MacroStep(action="press", args={"keys": "ctrl+s"}, description="Press shortcut 'ctrl+s'"),
        ]
        macro = Macro(
            name="save_note",
            description="Type and save note in Notepad",
            steps=steps,
            target_apps=["Notepad"],
        )

        d = macro.to_dict()
        restored = Macro.from_dict(d)
        self.assertEqual(restored.name, "save_note")
        self.assertEqual(len(restored.steps), 3)
        self.assertIn("save_note", restored.format_plan())
        self.assertIn("Notepad", restored.format_plan())

    def test_03_event_synthesis_and_coalescing(self):
        # Simulated raw events stream
        raw_events = [
            RawEvent(kind="focus_window", data={"title": "Untitled - Notepad"}),
            RawEvent(kind="click", data={"x": 200, "y": 300, "button": "left", "element_name": "Text Editor", "control_type": "Document"}),
            # Key presses: 'h', 'i', 'space', 't', 'h', 'e', 'r', 'e'
            RawEvent(kind="key_down", data={"name": "h"}),
            RawEvent(kind="key_up", data={"name": "h"}),
            RawEvent(kind="key_down", data={"name": "i"}),
            RawEvent(kind="key_up", data={"name": "i"}),
            RawEvent(kind="key_down", data={"name": "space"}),
            RawEvent(kind="key_up", data={"name": "space"}),
            RawEvent(kind="key_down", data={"name": "t"}),
            RawEvent(kind="key_up", data={"name": "t"}),
            RawEvent(kind="key_down", data={"name": "h"}),
            RawEvent(kind="key_up", data={"name": "h"}),
            RawEvent(kind="key_down", data={"name": "e"}),
            RawEvent(kind="key_up", data={"name": "e"}),
            RawEvent(kind="key_down", data={"name": "r"}),
            RawEvent(kind="key_up", data={"name": "r"}),
            RawEvent(kind="key_down", data={"name": "e"}),
            RawEvent(kind="key_up", data={"name": "e"}),
            # Hotkey: ctrl+s
            RawEvent(kind="key_down", data={"name": "ctrl"}),
            RawEvent(kind="key_down", data={"name": "s"}),
            RawEvent(kind="key_up", data={"name": "s"}),
            RawEvent(kind="key_up", data={"name": "ctrl"}),
        ]

        macro = self.recorder._synthesize_macro("test_workflow", "Test Note", raw_events)
        self.assertEqual(macro.name, "test_workflow")
        self.assertIn("Notepad", macro.target_apps)

        # Check synthesized steps:
        # Step 1: focus_window
        # Step 2: click
        # Step 3: type "hi there"
        # Step 4: press "ctrl+s"
        actions = [s.action for s in macro.steps]
        self.assertEqual(actions, ["focus_window", "click", "type", "press"])

        type_step = macro.steps[2]
        self.assertEqual(type_step.args["text"], "hi there")

        press_step = macro.steps[3]
        self.assertEqual(press_step.args["keys"], "ctrl+s")

    def test_04_manager_persistence_and_memory_sync(self):
        macro = Macro(
            name="export_report",
            description="Export daily metrics report",
            steps=[MacroStep(action="type", args={"text": "export_all"})],
            target_apps=["Excel"],
        )

        # Save macro to disk
        path = self.mgr.save_macro(macro, sync_memory=True)
        self.assertTrue(path.exists())

        # Load macro
        loaded = self.mgr.load_macro("export_report")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.name, "export_report")

        # List macros
        macros = self.mgr.list_macros()
        self.assertEqual(len(macros), 1)
        self.assertEqual(macros[0].name, "export_report")

        # Delete macro
        del_ok = self.mgr.delete_macro("export_report")
        self.assertTrue(del_ok)
        self.assertIsNone(self.mgr.load_macro("export_report"))

    def test_05_player_parameter_substitution(self):
        step = MacroStep(
            action="type",
            args={"text": "User {user_id} requested {item_name}."},
            description="Parameterized log entry",
        )
        macro = Macro(
            name="param_macro",
            description="Test parameter replacement",
            steps=[step],
        )

        executed_args = []

        def mock_execute_step(s, speed, params, pyautogui):
            args_copy = s.args.copy()
            for k, v in list(args_copy.items()):
                if isinstance(v, str):
                    for p_key, p_val in params.items():
                        args_copy[k] = args_copy[k].replace(f"{{{p_key}}}", str(p_val))
            executed_args.append(args_copy)

        self.player._execute_step = mock_execute_step
        res = self.player.play(
            macro,
            speed=1.0,
            params={"user_id": "42", "item_name": "quantum_core"},
        )
        self.assertTrue(res["ok"])
        self.assertEqual(len(executed_args), 1)
        self.assertEqual(executed_args[0]["text"], "User 42 requested quantum_core.")

    def test_06_tool_action_registry(self):
        self.assertIn("macro", ACTIONS_BY_NAME)
        cfg = Config()

        # 1. List (initially empty)
        r_list = registry.execute("macro", {"action": "list"}, None, cfg)
        self.assertTrue(r_list.ok)

        # 2. Save a macro directly via manager and view via registry
        m = Macro(
            name="daily_standup",
            description="Open Slack and post standup update",
            steps=[MacroStep(action="type", args={"text": "Today: finished macro engine."})],
        )
        self.mgr.save_macro(m, sync_memory=False)

        r_show = registry.execute("macro", {"action": "show", "name": "daily_standup"}, None, cfg)
        self.assertTrue(r_show.ok)
        self.assertIn("daily_standup", r_show.message)


if __name__ == "__main__":
    unittest.main()
