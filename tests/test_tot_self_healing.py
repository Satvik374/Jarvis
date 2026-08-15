"""Tests for Tree-of-Thought Autonomous Error Recovery & Self-Healing Engine."""

import unittest
from unittest.mock import Mock, patch

from jarvis.agent.tree_of_thought import (
    ErrorCategory,
    ErrorClassifier,
    ErrorDiagnosis,
    RecoveryAction,
    SelfHealingDirector,
    ThoughtNode,
    TreeOfThoughtEngine,
)
from jarvis.config import Config, SafetyConfig
from jarvis.tools import registry
from jarvis.tools.schema import ACTIONS_BY_NAME


class ErrorClassifierTests(unittest.TestCase):
    def test_01_classify_success(self):
        diag = ErrorClassifier.classify(
            action="click",
            args={"element_id": "btn_submit"},
            result_message="clicked element",
            is_ok=True,
            screen_changed=True,
            before_window="App",
            after_window="App",
        )
        self.assertEqual(diag.category, ErrorCategory.NONE)
        self.assertEqual(diag.suggested_strategy, RecoveryAction.NOOP)

    def test_02_classify_focus_loss(self):
        diag = ErrorClassifier.classify(
            action="click",
            args={"element_id": "btn_save"},
            result_message="clicked",
            is_ok=False,
            screen_changed=True,
            before_window="Notepad - Untitled",
            after_window="Program Manager (Desktop)",
        )
        self.assertEqual(diag.category, ErrorCategory.FOCUS_LOST)
        self.assertEqual(diag.suggested_strategy, RecoveryAction.REFOCUS_TARGET)
        self.assertIn("Refocus", diag.advice_prompt)

    def test_03_classify_ui_not_found(self):
        diag = ErrorClassifier.classify(
            action="click",
            args={"element_id": "submit_btn_12"},
            result_message="Element not found on active screen",
            is_ok=False,
            screen_changed=False,
            before_window="Chrome",
            after_window="Chrome",
        )
        self.assertEqual(diag.category, ErrorCategory.UI_NOT_FOUND)
        self.assertEqual(diag.suggested_strategy, RecoveryAction.SWITCH_TO_KEYBOARD)
        self.assertIn("Do NOT retry clicking", diag.advice_prompt)

    def test_04_classify_stuck_loop(self):
        diag = ErrorClassifier.classify(
            action="click",
            args={"element_id": "btn_ok"},
            result_message="clicked",
            is_ok=True,
            screen_changed=False,
            before_window="App",
            after_window="App",
            consecutive_repeats=2,
        )
        self.assertEqual(diag.category, ErrorCategory.NO_EFFECT_OR_LOOP)
        self.assertEqual(diag.suggested_strategy, RecoveryAction.BACKTRACK_AND_BRANCH)

    def test_05_classify_timeout_and_permission(self):
        diag_timeout = ErrorClassifier.classify(
            action="wait_for",
            args={"seconds": 10},
            result_message="Operation timed out waiting for element",
            is_ok=False,
            screen_changed=False,
            before_window="App",
            after_window="App",
        )
        self.assertEqual(diag_timeout.category, ErrorCategory.TIMEOUT_OR_HANG)

        diag_perm = ErrorClassifier.classify(
            action="run_command",
            args={"command": "net start service"},
            result_message="Access is denied. Administrative privileges required.",
            is_ok=False,
            screen_changed=False,
            before_window="cmd",
            after_window="cmd",
        )
        self.assertEqual(diag_perm.category, ErrorCategory.PERMISSION_OR_ACCESS)


class TreeOfThoughtEngineTests(unittest.TestCase):
    def test_01_tree_expansion_and_scoring(self):
        engine = TreeOfThoughtEngine()
        root = engine.root
        self.assertEqual(root.depth, 0)
        self.assertEqual(root.status, "ACTIVE")

        # Step 1: Success
        n1 = engine.record_step(
            thought="Open Notepad",
            action="open_app",
            args={"name": "notepad"},
            is_ok=True,
            result_message="Opened Notepad",
            active_window="Notepad",
            diagnosis=ErrorDiagnosis(ErrorCategory.NONE, 1.0, "ok", RecoveryAction.NOOP, ""),
        )
        self.assertEqual(n1.depth, 1)
        self.assertEqual(n1.status, "ACTIVE")
        self.assertEqual(n1.score, 1.0)
        self.assertEqual(engine.current_node, n1)

        # Step 2: Failure
        n2 = engine.record_step(
            thought="Click missing menu item",
            action="click",
            args={"element_id": "missing_id"},
            is_ok=False,
            result_message="Element not found",
            active_window="Notepad",
            diagnosis=ErrorDiagnosis(ErrorCategory.UI_NOT_FOUND, 0.95, "missing", RecoveryAction.SWITCH_TO_KEYBOARD, "try keyboard"),
        )
        self.assertEqual(n2.depth, 2)
        self.assertEqual(n2.status, "FAILED")
        self.assertLess(n2.score, 1.0)

    def test_02_cycle_detection(self):
        engine = TreeOfThoughtEngine()
        diag = ErrorDiagnosis(ErrorCategory.NONE, 1.0, "ok", RecoveryAction.NOOP, "")

        engine.record_step("click save", "click", {"element_id": "save_btn"}, True, "clicked", "App", diag)
        self.assertEqual(engine.detect_cycle(), 0)

        # Repeat identical action
        engine.record_step("click save again", "click", {"element_id": "save_btn"}, True, "clicked", "App", diag)
        self.assertEqual(engine.detect_cycle(), 1)

        # Repeat third time
        engine.record_step("click save 3rd time", "click", {"element_id": "save_btn"}, True, "clicked", "App", diag)
        self.assertEqual(engine.detect_cycle(), 2)

    def test_03_backtrack_to_healthy_ancestor(self):
        engine = TreeOfThoughtEngine()
        diag_ok = ErrorDiagnosis(ErrorCategory.NONE, 1.0, "ok", RecoveryAction.NOOP, "")
        diag_fail = ErrorDiagnosis(ErrorCategory.UI_NOT_FOUND, 0.9, "fail", RecoveryAction.SWITCH_TO_KEYBOARD, "")

        n1 = engine.record_step("Healthy Step 1", "press", {"key": "enter"}, True, "ok", "App", diag_ok)
        n2 = engine.record_step("Failed Step 2", "click", {"id": "bad"}, False, "err", "App", diag_fail)

        healthy = engine.backtrack_to_healthy_node()
        self.assertEqual(healthy.id, n1.id)


class SelfHealingDirectorTests(unittest.TestCase):
    def test_01_diagnose_and_generate_guidance(self):
        director = SelfHealingDirector(config_self_healing=True)

        diag, action_taken = director.diagnose_and_guide(
            thought="Click save",
            action="click",
            args={"element_id": "save_btn"},
            is_ok=False,
            result_message="element not found on active window",
            screen_changed=False,
            before_window="Word",
            after_window="Word",
        )

        self.assertEqual(diag.category, ErrorCategory.UI_NOT_FOUND)
        note = director.get_healing_note(diag, action_taken)
        self.assertIn("SELF-HEALING NOTICE", note)
        self.assertIn("Do NOT retry clicking", note)

    @patch.object(SelfHealingDirector, "perform_refocus", return_value=True)
    def test_02_active_refocus_healing(self, mock_refocus):
        director = SelfHealingDirector(config_self_healing=True, max_healing_attempts=3)
        director.last_target_window = "Notepad"

        diag, action_taken = director.diagnose_and_guide(
            thought="Typing into notepad",
            action="type_text",
            args={"text": "hello"},
            is_ok=False,
            result_message="Window not active",
            screen_changed=True,
            before_window="Notepad",
            after_window="Program Manager (Desktop)",
        )

        self.assertEqual(diag.category, ErrorCategory.FOCUS_LOST)
        self.assertIsNotNone(action_taken)
        self.assertIn("Auto-refocused", action_taken)
        self.assertEqual(director.healing_count, 1)

    def test_03_self_heal_action_in_registry(self):
        self.assertIn("self_heal", ACTIONS_BY_NAME)
        cfg = Config(safety=SafetyConfig(self_healing=True))

        # Test escape strategy
        with patch.object(SelfHealingDirector, "perform_escape", return_value=True):
            res_esc = registry.execute("self_heal", {"strategy": "escape"}, None, cfg)
            self.assertTrue(res_esc.ok)
            self.assertIn("Escape key", res_esc.message)

        # Test reset_state strategy
        with patch("pyautogui.keyUp") as mock_keyup:
            res_rst = registry.execute("self_heal", {"strategy": "reset_state"}, None, cfg)
            self.assertTrue(res_rst.ok)
            self.assertEqual(mock_keyup.call_count, 4)


if __name__ == "__main__":
    unittest.main()
