"""Unified Memory Manager for Jarvis.

Coordinates the Vector Store, Knowledge Graph, and Hybrid RAG retrieval engine,
while ensuring 100% backward-compatible synchronization with `memory.txt`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..utils import logging as log
from ..utils.paths import state_root
from .hybrid_rag import HybridRAG, RAGResult
from .knowledge_graph import KnowledgeGraph
from .obsidian import ObsidianVault, VaultUnavailable, get_vault
from .vector_store import EmbeddingEngine, MemoryRecord, VectorStore


def get_default_db_path() -> Path:
    return state_root() / "jarvis_memory.db"


def get_default_memory_path() -> Path:
    return state_root() / "memory.txt"


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
        #: The Obsidian vault is folded into the index once per process, on the
        #: first RAG read - see ensure_obsidian_index().
        self._obsidian_synced = False

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

    def extract_and_remember(self, text: str, source: str = "auto") -> list[str]:
        """Automatically extract user preferences, facts, and key context from natural conversation."""
        if not text or len(text.strip()) < 5:
            return []

        import re
        patterns = [
            r"(?:i prefer|i like|always use|prefer to use|default to)\s+([^.!?\n]+)",
            r"(?:my name is|call me|i am)\s+([^.!?\n]+)",
            r"(?:my project is (?:at|located in)|project path is)\s+([^.!?\n]+)",
            r"(?:my email is|contact me at)\s+([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)",
        ]

        extracted = []
        for pat in patterns:
            for match in re.finditer(pat, text, re.IGNORECASE):
                fact = match.group(0).strip()
                if len(fact) > 8 and fact not in extracted:
                    self.remember(fact, category="user_preference", sync_file=True)
                    extracted.append(fact)

        return extracted

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

    # ------------------------------------------------------------------ #
    # RAG Retrieval & Prompt Formatting
    # ------------------------------------------------------------------ #

    def get_rag_context(self, query: str, max_chars: int = 3500) -> str:
        """Retrieve and format token-efficient prompt context for the query/task."""
        self.ensure_obsidian_index()
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
    # Obsidian vault (optional memory source and sink)
    # ------------------------------------------------------------------ #

    def obsidian_vault(self, cfg: Any = None) -> Optional[ObsidianVault]:
        """The configured vault, or None when Obsidian is not in use."""
        return get_vault(cfg)

    def sync_obsidian(self, cfg: Any = None) -> Tuple[int, int]:
        """Index every vault note into the Vector Store and Knowledge Graph."""
        vault = self.obsidian_vault(cfg)
        if vault is None:
            return 0, 0
        indexed, links = vault.index(self.vector_store, self.knowledge_graph)
        log.ok(f"Indexed {indexed} Obsidian note(s) and {links} link(s) into memory.")
        return indexed, links

    def ensure_obsidian_index(self, cfg: Any = None) -> int:
        """Fold the vault into memory once per process, before the first RAG read.

        Guarded on purpose: this sits on the memory path of every step, so the
        vault is walked exactly once and then never again for this process.
        Every failure is logged and swallowed - a broken vault must not be able
        to break the agent loop.
        """
        if self._obsidian_synced:
            return 0
        self._obsidian_synced = True  # before the work, so re-entry cannot recurse
        try:
            return self.sync_obsidian(cfg)[0]
        except VaultUnavailable as exc:
            log.warn(f"Obsidian vault unavailable: {exc}")
        except Exception as exc:  # noqa: BLE001 - memory is best-effort
            log.warn(f"Obsidian indexing failed: {exc}")
        return 0

    def save_obsidian_note(
        self,
        name: str,
        content: str,
        mode: str = "append",
        heading: str = "",
        tags: Iterable[str] = (),
        cfg: Any = None,
    ) -> str:
        """Write back to the vault and refresh that note's memory record.

        ``mode`` is one of ``append`` (default, never rewrites prior text),
        ``create`` (refuses to clobber), or ``daily`` (today's daily note).
        """
        vault = self.obsidian_vault(cfg)
        if vault is None:
            raise VaultUnavailable(
                "No Obsidian vault is configured. Set memory.obsidian_vault in config.yaml "
                "or the JARVIS_OBSIDIAN_VAULT environment variable to the folder that holds "
                "your notes."
            )
        choice = (mode or "append").strip().lower()
        if choice in {"create", "new"}:
            relative = vault.create(name, content, tags=tags)
        elif choice in {"daily", "journal"}:
            relative = vault.daily(content)
        elif choice in {"append", "add"}:
            relative = vault.append(name, content, heading=heading)
        else:
            raise ValueError(f"Unknown note mode '{mode}'; use append, create or daily.")
        try:
            # Only the note that changed is re-indexed; a full walk would cost
            # more than the write on a large vault.
            vault.index(self.vector_store, self.knowledge_graph, only=[relative])
        except Exception as exc:  # noqa: BLE001 - the note is saved either way
            log.warn(f"Saved {relative}, but re-indexing it failed: {exc}")
        return relative

    def obsidian_status(self, cfg: Any = None) -> Dict[str, Any]:
        """Whether Obsidian is configured, usable, and how much is indexed."""
        try:
            vault = self.obsidian_vault(cfg)
        except VaultUnavailable as exc:
            return {"configured": True, "available": False, "detail": str(exc)}
        if vault is None:
            return {
                "configured": False,
                "available": False,
                "detail": "Set memory.obsidian_vault or JARVIS_OBSIDIAN_VAULT to enable "
                          "Obsidian notes as memory.",
            }
        status: Dict[str, Any] = dict(vault.stats())
        status.update({
            "configured": True,
            "available": True,
            "indexed": len(self.vector_store.get_all(doc_type="note")),
            "synced": self._obsidian_synced,
        })
        return status

    # ------------------------------------------------------------------ #
    # File Synchronization (memory.txt <-> SQLite Vector/Graph)
    # ------------------------------------------------------------------ #

    def sync_from_file(self, file_path: Optional[Path | str] = None) -> int:
        """Parse memory.txt and load its facts into the Vector Store & Graph."""
        path = Path(file_path) if file_path else self.memory_path
        if not path.exists():
            return 0

        text = path.read_text(encoding="utf-8")
        from ..agent.memory import parse_memory_text

        facts = parse_memory_text(text)
        fact_count = 0

        for f in facts:
            f_clean = f.strip()
            if f_clean:
                cat = "fact"
                if f_clean.startswith("[") and "]" in f_clean:
                    cat = f_clean[1:f_clean.index("]")].strip().lower()
                self.vector_store.add_record(f_clean, category=cat, doc_type="fact")
                self.knowledge_graph.extract_and_index(f_clean, category=cat)
                fact_count += 1

        log.ok(f"Synced memory from file: {fact_count} facts.")
        return fact_count

    def _sync_all_to_memory_file(self) -> None:
        """Write current database state back to memory.txt for human readability.

        Nothing is evicted to fit a budget here: permanent memories are exactly
        the ones the user asked Jarvis to keep, so the file grows with them.
        """
        facts = [r.content for r in self.vector_store.get_all(doc_type="fact")]

        from ..agent.memory import format_memory_text

        self.memory_path.parent.mkdir(parents=True, exist_ok=True)
        self.memory_path.write_text(format_memory_text(facts), encoding="utf-8")

    def get_stats(self) -> Dict[str, Any]:
        """Return overview stats of memory records and knowledge graph."""
        vec_count = self.vector_store.count()
        facts_count = len(self.vector_store.get_all(doc_type="fact"))
        graph_stats = self.knowledge_graph.count_stats()
        return {
            "total_vectors": vec_count,
            "facts_count": facts_count,
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
