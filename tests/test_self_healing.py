import unittest
from unittest.mock import Mock, patch

from jarvis.agent.brain import complete_with_retry
from jarvis.tools import registry
from jarvis.tools.schema import ACTIONS_BY_NAME


class RetryTests(unittest.TestCase):
    @patch("jarvis.agent.brain.time.sleep")
    def test_transient_failure_is_retried(self, _sleep):
        brain = Mock()
        brain.complete.side_effect = [RuntimeError("503"), RuntimeError("503"),
                                      '{"action":"finish"}']
        out = complete_with_retry(brain, "sys", [])
        self.assertEqual(out, '{"action":"finish"}')
        self.assertEqual(brain.complete.call_count, 3)

    @patch("jarvis.agent.brain.time.sleep")
    def test_persistent_failure_raises(self, _sleep):
        brain = Mock()
        brain.complete.side_effect = RuntimeError("bad key")
        with self.assertRaises(RuntimeError):
            complete_with_retry(brain, "sys", [], tries=3)
        self.assertEqual(brain.complete.call_count, 3)


class SelfUpgradeTests(unittest.TestCase):
    def test_registered_in_schema_and_registry(self):
        self.assertIn("self_upgrade", ACTIONS_BY_NAME)
        self.assertIn("self_upgrade", registry._HANDLERS)
        # every schema action has a handler and vice versa
        self.assertEqual(set(ACTIONS_BY_NAME), set(registry._HANDLERS))

    def test_empty_description_refused(self):
        res = registry._h_self_upgrade({}, None, None)
        self.assertFalse(res.ok)
        self.assertIn("description", res.message)


if __name__ == "__main__":
    unittest.main()
