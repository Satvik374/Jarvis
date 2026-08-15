"""Unified Memory Manager for Jarvis.

Coordinates the Vector Store, Knowledge Graph, and Hybrid RAG retrieval engine,
while ensuring 100% backward-compatible synchronization with `memory.txt`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..utils import logging as log
from .hybrid_rag import HybridRAG, RAGResult
from .knowledge_graph import KnowledgeGraph
from .vector_store import EmbeddingEngine, MemoryRecord, VectorStore


def _get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def get_default_db_path() -> Path:
    return _get_project_root() / "jarvis_memory.db"


def get_default_memory_path() -> Path:
    return _get_project_root() / "memory.txt"


class MemoryManager:
    """Central manager for Vector RAG, Knowledge Graph, and Memory synchronization."""

    def __init__(
        self,
        db_path: Optional[Path | str] = None,
        memory_path: Optional[Path | str] = None,
        embedding_backend: str = "auto",
    ):
        self.memory_path = Path(memory_path) if memory_path else get_default_memory_path()
        if db_path:
            self.db_path = Path(db_path)
        else:
            if memory_path and Path(memory_path) != get_default_memory_path():
                p = Path(memory_path)
                self.db_path = p.parent / f"{p.stem}_memory.db"
            else:
                self.db_path = get_default_db_path()

        embedder = EmbeddingEngine(backend=embedding_backend)
        self.vector_store = VectorStore(self.db_path, embedder=embedder)
        self.knowledge_graph = KnowledgeGraph(self.db_path)
        self.rag = HybridRAG(self.vector_store, self.knowledge_graph)

        # Auto-migrate on initial startup if database is empty but memory.txt exists
        self._auto_migrate_if_needed()

    def _auto_migrate_if_needed(self) -> None:
        if self.vector_store.count() == 0 and self.memory_path.exists():
            log.info("Migrating existing memory.txt into Vector Store & Knowledge Graph...")
            self.sync_from_file(self.memory_path)

    # ------------------------------------------------------------------ #
    # High-Level Memory Operations
    # ------------------------------------------------------------------ #

    def remember(
        self,
        fact: str,
        category: str = "fact",
        entity: Optional[str] = None,
        relation: Optional[str] = None,
        target_entity: Optional[str] = None,
        sync_file: bool = True,
    ) -> str:
        """Store a fact permanently in Vector Store, Knowledge Graph, and memory.txt."""
        fact_str = (fact or "").strip()
        if not fact_str:
            return "No fact provided to remember."

        cat = (category or "fact").strip().lower()
        content = f"[{cat}] {fact_str}" if cat and not fact_str.startswith("[") else fact_str

        # Check if existing record with similar content exists and update it
        existing_recs = self.vector_store.get_all(doc_type="fact")
        updated = False
        norm_fact = fact_str.lower()
        for rec in existing_recs:
            r_norm = rec.content.lower()
            if norm_fact in r_norm or r_norm in norm_fact:
                self.vector_store.delete_record(rec.id)
                self.vector_store.add_record(
                    content=content,
                    category=cat,
                    doc_type="fact",
                    metadata={"source": "remember", "category": cat},
                    record_id=rec.id,
                )
                updated = True
                break

        if not updated:
            self.vector_store.add_record(
                content=content,
                category=cat,
                doc_type="fact",
                metadata={"source": "remember", "category": cat},
            )

        # 2. Extract & Insert into Knowledge Graph
        if entity and relation and target_entity:
            self.knowledge_graph.add_relation(
                source_name=entity,
                relation_type=relation,
                target_name=target_entity,
                context=content,
            )
        else:
            self.knowledge_graph.extract_and_index(content, category=cat)

        # 3. Synchronize to memory.txt
        if sync_file:
            self._sync_all_to_memory_file()

        action_type = "Updated" if updated else "Remembered"
        log.ok(f"{action_type} memory (Vector + Graph): {content}")
        return f"{action_type} permanent memory: {content}"

    def forget(self, target: str, sync_file: bool = True) -> str:
        """Remove facts matching target from Vector Store, Knowledge Graph, and memory.txt."""
        target_str = (target or "").strip().lower()
        if not target_str:
            return "No target provided to forget."

        # 1. Find matching records
        all_facts = self.vector_store.get_all(doc_type="fact")
        removed_contents = []
        for rec in all_facts:
            if target_str in rec.content.lower():
                self.vector_store.delete_record(rec.id)
                removed_contents.append(rec.content)

        # 2. Synchronize to memory.txt
        if sync_file:
            self._sync_all_to_memory_file()

        if removed_contents:
            log.ok(f"Forgot memory: {removed_contents}")
            return f"Forgot {len(removed_contents)} memory item(s) matching '{target}': {', '.join(removed_contents)}"
        return f"No permanent memory found matching '{target}'."

    def append_learned_plan(
        self,
        task: str,
        plan: dict,
        max_chars: int = 4000,
        sync_file: bool = True,
    ) -> None:
        """Append a verified-successful plan to Vector Store and memory.txt."""
        task_str = (task or "").strip()
        if not task_str or not plan:
            return

        entry = (
            f"- Learned Task: {task_str}\n"
            f"  Successful Plan: {plan.get('name', '')}\n"
            f"  Approach: {plan.get('description', '')}"
        )

        # Deduplication check
        existing_plans = self.vector_store.get_all(doc_type="learned_plan")
        want = task_str.lower()
        for ep in existing_plans:
            lines = ep.content.lower().splitlines()
            for line in lines:
                if line.strip().startswith("- learned task:"):
                    stored = line.split("- learned task:")[1].strip()
                    if stored and (want in stored or stored in want):
                        return  # Already learned

        # 1. Save to Vector Store
        self.vector_store.add_record(
            content=entry,
            category="learned_plan",
            doc_type="learned_plan",
            metadata={"task": task_str, "plan_name": plan.get("name", "")},
        )

        # 2. Extract any useful entity relationships from the task
        self.knowledge_graph.extract_and_index(task_str, category="learned_task")

        # 3. Synchronize to memory.txt
        if sync_file:
            self._sync_all_to_memory_file(max_chars=max_chars)

        log.ok("Saved verified-successful plan to memory (Vector + Graph learned).")

    def evict_learned_plan(self, task: str, sync_file: bool = True) -> None:
        """Evict a learned plan that failed."""
        task_str = (task or "").strip().lower()
        if not task_str:
            return

        existing_plans = self.vector_store.get_all(doc_type="learned_plan")
        for ep in existing_plans:
            lines = ep.content.lower().splitlines()
            for line in lines:
                if line.strip().startswith("- learned task:"):
                    stored = line.split("- learned task:")[1].strip()
                    if stored and (task_str in stored or stored in task_str):
                        self.vector_store.delete_record(ep.id)
                        break

        if sync_file:
            self._sync_all_to_memory_file()
        log.info(f"Un-learned stale plan for task: {task[:50]}")

    # ------------------------------------------------------------------ #
    # RAG Retrieval & Prompt Formatting
    # ------------------------------------------------------------------ #

    def get_rag_context(self, query: str, max_chars: int = 3500) -> str:
        """Retrieve and format token-efficient prompt context for the query/task."""
        return self.rag.format_prompt_context(query, max_chars=max_chars)

    def search_semantic(
        self,
        query: str,
        top_k: int = 5,
        min_score: float = 0.1,
    ) -> List[Tuple[MemoryRecord, float]]:
        """Direct semantic search against vector records."""
        return self.vector_store.search(query=query, top_k=top_k, min_score=min_score)

    def query_graph(self, entity_name: str) -> Dict[str, Any]:
        """Query knowledge graph neighborhood for an entity."""
        ent = self.knowledge_graph.get_entity(entity_name)
        if not ent:
            return {"entity": None, "relations": []}
        return self.knowledge_graph.query_subgraph([ent.name], max_hops=1)

    # ------------------------------------------------------------------ #
    # File Synchronization (memory.txt <-> SQLite Vector/Graph)
    # ------------------------------------------------------------------ #

    def sync_from_file(self, file_path: Optional[Path | str] = None) -> Tuple[int, int]:
        """Parse memory.txt and load all facts & plans into SQLite Vector Store & Graph."""
        path = Path(file_path) if file_path else self.memory_path
        if not path.exists():
            return 0, 0

        text = path.read_text(encoding="utf-8")
        from ..agent.memory import parse_memory_text

        facts, plans = parse_memory_text(text)
        fact_count = 0
        plan_count = 0

        for f in facts:
            f_clean = f.strip()
            if f_clean:
                cat = "fact"
                if f_clean.startswith("[") and "]" in f_clean:
                    cat = f_clean[1:f_clean.index("]")].strip().lower()
                self.vector_store.add_record(f_clean, category=cat, doc_type="fact")
                self.knowledge_graph.extract_and_index(f_clean, category=cat)
                fact_count += 1

        for p in plans:
            p_clean = p.strip()
            if p_clean:
                self.vector_store.add_record(p_clean, category="learned_plan", doc_type="learned_plan")
                plan_count += 1

        log.ok(f"Synced memory from file: {fact_count} facts, {plan_count} learned plans.")
        return fact_count, plan_count

    def _sync_all_to_memory_file(self, max_chars: int = 4000) -> None:
        """Write current database state back to memory.txt for human readability."""
        facts_records = self.vector_store.get_all(doc_type="fact")
        plans_records = self.vector_store.get_all(doc_type="learned_plan")

        facts = [r.content for r in facts_records]
        plans = [r.content for r in plans_records]

        from ..agent.memory import format_memory_text

        formatted_text = format_memory_text(facts, plans)
        while len(formatted_text) > max_chars and len(plans) > 1:
            oldest_plan = plans_records.pop(0)
            self.vector_store.delete_record(oldest_plan.id)
            plans.pop(0)
            formatted_text = format_memory_text(facts, plans)

        self.memory_path.parent.mkdir(parents=True, exist_ok=True)
        self.memory_path.write_text(formatted_text, encoding="utf-8")

    def get_stats(self) -> Dict[str, Any]:
        """Return overview stats of memory records and knowledge graph."""
        vec_count = self.vector_store.count()
        facts_count = len(self.vector_store.get_all(doc_type="fact"))
        plans_count = len(self.vector_store.get_all(doc_type="learned_plan"))
        graph_stats = self.knowledge_graph.count_stats()
        return {
            "total_vectors": vec_count,
            "facts_count": facts_count,
            "learned_plans_count": plans_count,
            "graph_entities": graph_stats.get("entities", 0),
            "graph_relations": graph_stats.get("relations", 0),
            "db_path": str(self.db_path),
            "memory_file": str(self.memory_path),
        }


# Singleton instance
_GLOBAL_MANAGER: Optional[MemoryManager] = None


def get_memory_manager(
    db_path: Optional[Path | str] = None,
    memory_path: Optional[Path | str] = None,
) -> MemoryManager:
    global _GLOBAL_MANAGER
    if db_path is not None or (memory_path is not None and Path(memory_path) != get_default_memory_path()):
        return MemoryManager(db_path=db_path, memory_path=memory_path)

    if _GLOBAL_MANAGER is None:
        _GLOBAL_MANAGER = MemoryManager()
    return _GLOBAL_MANAGER
