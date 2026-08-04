import tempfile
import unittest
from pathlib import Path

from jarvis.agent.memory import (
    parse_memory_text,
    format_memory_text,
    remember_fact,
    forget_fact,
    append_learned_plan,
    evict_learned_plan,
)
from jarvis.tools import registry
from jarvis.tools.schema import ACTIONS_BY_NAME


class MemoryManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, mode="w+", encoding="utf-8")
        self.tmp_path = Path(self.tmp.name)
        self.tmp.close()

    def tearDown(self):
        if self.tmp_path.exists():
            self.tmp_path.unlink()

    def test_remember_and_forget_fact(self):
        # 1. Remember a fact
        res1 = remember_fact(self.tmp_path, "User prefers dark mode UI", category="preference")
        self.assertIn("Remembered permanent memory", res1)

        text1 = self.tmp_path.read_text(encoding="utf-8")
        self.assertIn("=== PERMANENT MEMORIES ===", text1)
        self.assertIn("[preference] User prefers dark mode UI", text1)

        # 2. Remember another fact
        remember_fact(self.tmp_path, "User's name is Alex", category="user_info")
        text2 = self.tmp_path.read_text(encoding="utf-8")
        self.assertIn("[user_info] User's name is Alex", text2)

        # 3. Forget a fact
        res2 = forget_fact(self.tmp_path, "dark mode")
        self.assertIn("Forgot 1 memory item(s)", res2)

        text3 = self.tmp_path.read_text(encoding="utf-8")
        self.assertNotIn("dark mode", text3)
        self.assertIn("Alex", text3)

    def test_capping_does_not_evict_permanent_memories(self):
        remember_fact(self.tmp_path, "CRITICAL: Never delete database without asking", category="rule")

        # Append many learned task plans until size exceeds max_chars
        for i in range(20):
            append_learned_plan(
                self.tmp_path,
                task=f"task_{i} with a very long description that takes up space",
                plan={"name": f"plan_{i}", "description": "x" * 200},
                max_chars=500,
            )

        text = self.tmp_path.read_text(encoding="utf-8")
        # Permanent facts MUST be preserved despite capping
        self.assertIn("CRITICAL: Never delete database without asking", text)
        facts, plans = parse_memory_text(text)
        self.assertEqual(len(facts), 1)
        self.assertTrue(len(plans) > 0)

    def test_evict_learned_plan(self):
        append_learned_plan(self.tmp_path, "open notepad", {"name": "P1", "description": "D1"})
        append_learned_plan(self.tmp_path, "open calc", {"name": "P2", "description": "D2"})

        text1 = self.tmp_path.read_text(encoding="utf-8")
        self.assertIn("open notepad", text1)
        self.assertIn("open calc", text1)

        evict_learned_plan(self.tmp_path, "open notepad")
        text2 = self.tmp_path.read_text(encoding="utf-8")
        self.assertNotIn("open notepad", text2)
        self.assertIn("open calc", text2)

    def test_schema_and_registry_handlers(self):
        self.assertIn("remember", ACTIONS_BY_NAME)
        self.assertIn("forget", ACTIONS_BY_NAME)

        res_rem = registry.execute("remember", {"fact": "User speaks Esperanto", "category": "lang"}, None, None)
        self.assertTrue(res_rem.ok)
        self.assertIn("permanent memory", res_rem.message)

        res_for = registry.execute("forget", {"target": "Esperanto"}, None, None)
        self.assertTrue(res_for.ok)
        self.assertIn("Forgot", res_for.message)


if __name__ == "__main__":
    unittest.main()
