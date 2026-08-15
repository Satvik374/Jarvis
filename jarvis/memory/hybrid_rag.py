"""Hybrid RAG Retrieval Engine for Jarvis.

Combines Vector Semantic Search, Knowledge Graph Subgraph Expansion, and
Keyword/Exact matching using Reciprocal Rank Fusion (RRF). Formats a token-efficient
prompt context block for both agent loop planning and conversational chat.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .knowledge_graph import KnowledgeGraph, Relation, Entity
from .vector_store import MemoryRecord, VectorStore


@dataclass
class RAGResult:
    query: str
    vector_records: List[tuple[MemoryRecord, float]] = field(default_factory=list)
    graph_relations: List[Relation] = field(default_factory=list)
    mentioned_entities: List[Entity] = field(default_factory=list)
    core_rules: List[MemoryRecord] = field(default_factory=list)
    learned_plans: List[MemoryRecord] = field(default_factory=list)

    def is_empty(self) -> bool:
        return (
            not self.vector_records
            and not self.graph_relations
            and not self.core_rules
            and not self.learned_plans
        )


class HybridRAG:
    """Hybrid RAG retriever combining vector similarity and graph relations."""

    def __init__(self, vector_store: VectorStore, knowledge_graph: KnowledgeGraph):
        self.vector_store = vector_store
        self.knowledge_graph = knowledge_graph

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        min_vector_score: float = 0.15,
        include_core_rules: bool = True,
    ) -> RAGResult:
        query_str = (query or "").strip()
        result = RAGResult(query=query_str)

        if not query_str:
            if include_core_rules:
                result.core_rules = self._get_core_rules()
            return result

        # 1. Vector Semantic Retrieval
        raw_vec_results = self.vector_store.search(
            query=query_str,
            top_k=top_k * 2,
            min_score=min_vector_score,
        )

        # Split vector results into facts and learned plans
        for rec, score in raw_vec_results:
            if rec.doc_type == "learned_plan":
                result.learned_plans.append(rec)
            else:
                result.vector_records.append((rec, score))

        # 2. Knowledge Graph Entity & Subgraph Expansion
        mentioned_entities = self.knowledge_graph.find_mentioned_entities(query_str)
        result.mentioned_entities = mentioned_entities

        if mentioned_entities:
            subgraph = self.knowledge_graph.query_subgraph(
                [e.name for e in mentioned_entities],
                max_hops=1,
            )
            result.graph_relations = subgraph.get("relations", [])[:8]

        # 3. Core Rules & Guardrails
        if include_core_rules:
            result.core_rules = self._get_core_rules()

        # 4. Limit vector records to top_k
        result.vector_records = result.vector_records[:top_k]
        result.learned_plans = result.learned_plans[:3]

        return result

    def _get_core_rules(self) -> List[MemoryRecord]:
        """Fetch critical persistent rules and language settings that should always be present."""
        all_recs = self.vector_store.get_all(doc_type="fact")
        core = []
        for r in all_recs:
            cat = (r.category or "").lower()
            content_lower = r.content.lower()
            if cat in {"rule", "lang", "safety"} or content_lower.startswith(("[rule]", "[lang]", "[safety]")):
                core.append(r)
        return core

    def format_prompt_context(self, query: str, max_chars: int = 3500) -> str:
        """Format retrieved vector + graph context into a clean, token-bounded prompt block."""
        rag_data = self.retrieve(query, top_k=5, include_core_rules=True)

        if rag_data.is_empty():
            return (
                "\n\n=== PERSISTENT MEMORY (persists across runs) ===\n"
                "(No specific memory found. Use 'remember' action to store facts/preferences.)\n"
                "================================================="
            )

        sections: List[str] = ["\n\n=== RELEVANT LONG-TERM MEMORY (Vector RAG + Knowledge Graph) ==="]

        # Section 1: Core Rules & Preferences
        if rag_data.core_rules:
            sections.append("[Core Rules & Baseline Preferences]")
            for rule in rag_data.core_rules:
                sections.append(f"- {rule.content}")

        # Section 2: Semantically Retrieved Facts
        shown_contents = {r.content for r in rag_data.core_rules}
        retrieved_facts = []
        for rec, score in rag_data.vector_records:
            if rec.content not in shown_contents:
                shown_contents.add(rec.content)
                retrieved_facts.append(f"- {rec.content}")

        if retrieved_facts:
            sections.append("\n[Context-Relevant Facts (Retrieved via RAG)]")
            sections.extend(retrieved_facts)

        # Section 3: Knowledge Graph Entity Relationships
        if rag_data.graph_relations:
            sections.append("\n[Knowledge Graph Connections]")
            for rel in rag_data.graph_relations:
                ctx_note = f" (context: {rel.context[:60]}...)" if rel.context and len(rel.context) > 20 else ""
                sections.append(f"- {rel.source_name} --[{rel.relation_type}]--> {rel.target_name}{ctx_note}")

        # Section 4: Relevant Learned Plans
        if rag_data.learned_plans:
            sections.append("\n[Relevant Learned Plans]")
            for plan in rag_data.learned_plans:
                sections.append(f"{plan.content}")

        sections.append("=================================================================")
        formatted = "\n".join(sections)

        # Cap length if necessary
        if len(formatted) > max_chars:
            formatted = formatted[:max_chars - 30] + "\n... (memory truncated)" + "\n================================================================="

        return formatted
