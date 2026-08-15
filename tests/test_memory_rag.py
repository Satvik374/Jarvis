import tempfile
import unittest
from pathlib import Path

from jarvis.memory.vector_store import VectorStore, EmbeddingEngine
from jarvis.memory.knowledge_graph import KnowledgeGraph
from jarvis.memory.hybrid_rag import HybridRAG
from jarvis.memory.manager import MemoryManager
from jarvis.agent.memory import (
    remember_fact,
    forget_fact,
    append_learned_plan,
    evict_learned_plan,
)
from jarvis.tools import registry
from jarvis.tools.schema import ACTIONS_BY_NAME


class VectorStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp_db.close()
        self.db_path = Path(self.tmp_db.name)
        self.embedder = EmbeddingEngine(backend="auto")
        self.store = VectorStore(self.db_path, embedder=self.embedder)

    def tearDown(self):
        if self.db_path.exists():
            self.db_path.unlink()

    def test_add_and_search(self):
        self.store.add_record(
            content="User prefers Spotify 'Peace' playlist when coding",
            category="preference",
            doc_type="fact",
        )
        self.store.add_record(
            content="Git repository is hosted at https://github.com/Satvik374/Jarvis.git",
            category="project",
            doc_type="fact",
        )

        self.assertEqual(self.store.count(), 2)

        # Search for music preference
        results = self.store.search("Spotify playlist", top_k=2)
        self.assertTrue(len(results) > 0)
        top_rec, score = results[0]
        self.assertIn("Spotify", top_rec.content)
        self.assertGreater(score, 0.2)

        # Search for Git repository
        git_results = self.store.search("where is git repo code?", top_k=2)
        self.assertTrue(len(git_results) > 0)
        self.assertIn("github.com", git_results[0][0].content)

    def test_delete_by_pattern(self):
        self.store.add_record("User prefers dark mode UI", category="preference")
        self.store.add_record("User prefers Vim keybindings", category="preference")
        self.assertEqual(self.store.count(), 2)

        deleted = self.store.delete_by_pattern("dark mode")
        self.assertEqual(deleted, 1)
        self.assertEqual(self.store.count(), 1)


class KnowledgeGraphTests(unittest.TestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp_db.close()
        self.db_path = Path(self.tmp_db.name)
        self.graph = KnowledgeGraph(self.db_path)

    def tearDown(self):
        if self.db_path.exists():
            self.db_path.unlink()

    def test_entities_and_relations(self):
        self.graph.add_or_update_entity("User", entity_type="person", aliases=["Satvik", "Admin"])
        self.graph.add_or_update_entity("Spotify", entity_type="app")
        self.graph.add_or_update_entity("Peace", entity_type="playlist")

        self.graph.add_relation("User", "prefers_playlist", "Peace")
        self.graph.add_relation("Spotify", "has_playlist", "Peace")

        # Check entity by alias
        user_ent = self.graph.get_entity("Satvik")
        self.assertIsNotNone(user_ent)
        self.assertEqual(user_ent.name, "User")

        # Query subgraph for User
        subgraph = self.graph.query_subgraph(["User"], max_hops=2)
        self.assertGreaterEqual(len(subgraph["entities"]), 2)
        rel_types = [r.relation_type for r in subgraph["relations"]]
        self.assertIn("prefers_playlist", rel_types)

    def test_extract_and_index(self):
        text = "Whenever the user asks to open Spotify, open Spotify in Chrome and play their 'Peace' playlist."
        extracted = self.graph.extract_and_index(text, category="preference")
        self.assertTrue(len(extracted) > 0)

        # Check that Spotify and Peace entities were created
        spotify_ent = self.graph.get_entity("Spotify")
        peace_ent = self.graph.get_entity("Peace")
        self.assertIsNotNone(spotify_ent)
        self.assertIsNotNone(peace_ent)


class HybridRAGTests(unittest.TestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp_db.close()
        self.db_path = Path(self.tmp_db.name)

        self.vector_store = VectorStore(self.db_path)
        self.knowledge_graph = KnowledgeGraph(self.db_path)
        self.rag = HybridRAG(self.vector_store, self.knowledge_graph)

    def tearDown(self):
        if self.db_path.exists():
            self.db_path.unlink()

    def test_hybrid_retrieval_and_formatting(self):
        # 1. Add core rule
        self.vector_store.add_record("[rule] Always verify task completion before summarizing", category="rule")

        # 2. Add preference fact & graph
        self.vector_store.add_record("[preference] User prefers Spotify 'Peace' playlist", category="preference")
        self.knowledge_graph.add_or_update_entity("Spotify", entity_type="app")
        self.knowledge_graph.add_or_update_entity("Peace", entity_type="playlist")
        self.knowledge_graph.add_relation("Spotify", "has_playlist", "Peace")

        # 3. Add learned plan
        self.vector_store.add_record(
            "- Learned Task: open Spotify and play music\n  Approach: open Chrome and navigate to Spotify",
            category="learned_plan",
            doc_type="learned_plan",
        )

        # Retrieve RAG context for a query
        prompt_block = self.rag.format_prompt_context("Can you play some music on Spotify?")
        self.assertIn("RELEVANT LONG-TERM MEMORY", prompt_block)
        self.assertIn("[rule] Always verify task completion", prompt_block)
        self.assertIn("Peace", prompt_block)


class MemoryManagerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp_db.close()
        self.db_path = Path(self.tmp_db.name)

        self.tmp_txt = tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="w+", encoding="utf-8")
        self.tmp_txt.write("=== PERMANENT MEMORIES ===\n- [lang] User speaks English\n")
        self.tmp_txt.close()
        self.txt_path = Path(self.tmp_txt.name)

        self.manager = MemoryManager(db_path=self.db_path, memory_path=self.txt_path)

    def tearDown(self):
        if self.db_path.exists():
            self.db_path.unlink()
        if self.txt_path.exists():
            self.txt_path.unlink()

    def test_remember_and_forget(self):
        # 1. Remember
        res = self.manager.remember("User's primary coding IDE is VS Code", category="preference")
        self.assertIn("Remembered permanent memory", res)

        # Check DB count and memory file
        self.assertGreaterEqual(self.manager.vector_store.count(), 1)
        txt_content = self.txt_path.read_text(encoding="utf-8")
        self.assertIn("VS Code", txt_content)

        # 2. Forget
        res_forget = self.manager.forget("VS Code")
        self.assertIn("Forgot", res_forget)
        txt_after = self.txt_path.read_text(encoding="utf-8")
        self.assertNotIn("VS Code", txt_after)

    def test_learned_plan_lifecycle(self):
        self.manager.append_learned_plan(
            task="push code to github repository",
            plan={"name": "Git Push Plan", "description": "Run git add, git commit, and git push."},
        )
        txt_content = self.txt_path.read_text(encoding="utf-8")
        self.assertIn("push code to github repository", txt_content)

        # Evict
        self.manager.evict_learned_plan("push code to github repository")
        txt_after = self.txt_path.read_text(encoding="utf-8")
        self.assertNotIn("push code to github repository", txt_after)

    def test_tool_actions(self):
        self.assertIn("memory_search", ACTIONS_BY_NAME)
        self.assertIn("graph_query", ACTIONS_BY_NAME)
        self.assertIn("remember", ACTIONS_BY_NAME)
        self.assertIn("forget", ACTIONS_BY_NAME)

        # Execute remember via registry
        r1 = registry.execute("remember", {"fact": "Project root is C:/Jarvis", "category": "project"}, None, None)
        self.assertTrue(r1.ok)

        # Execute memory_search via registry
        r2 = registry.execute("memory_search", {"query": "Project root"}, None, None)
        self.assertTrue(r2.ok)
        self.assertIn("C:/Jarvis", r2.message)

        # Execute graph_query via registry
        r3 = registry.execute("graph_query", {"entity": "Jarvis"}, None, None)
        self.assertTrue(r3.ok)


if __name__ == "__main__":
    unittest.main()
