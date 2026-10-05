import tempfile
import unittest
from pathlib import Path

from jarvis.agent.memory import (
    parse_memory_text,
    format_memory_text,
    remember_fact,
    forget_fact,
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

    def test_many_facts_do_not_evict_the_first(self):
        remember_fact(self.tmp_path, "CRITICAL: Never delete database without asking", category="rule")
        for i in range(20):
            remember_fact(self.tmp_path, f"Filler preference {i} for exercising the file")

        text = self.tmp_path.read_text(encoding="utf-8")
        # Permanent memories are kept forever; nothing caps or evicts them.
        self.assertIn("CRITICAL: Never delete database without asking", text)
        facts = parse_memory_text(text)
        self.assertEqual(len(facts), 21)

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
