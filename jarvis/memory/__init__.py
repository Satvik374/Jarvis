"""Long-Term Memory Subsystem for JARVIS.

Combines Vector Semantic Search (RAG) + Knowledge Graph (Entity-Relations)
+ Keyword/BM25 Matching into a unified, token-efficient long-term memory engine.
"""

from .vector_store import VectorStore, MemoryRecord
from .knowledge_graph import KnowledgeGraph, Entity, Relation
from .hybrid_rag import HybridRAG, RAGResult
from .manager import MemoryManager, get_memory_manager

__all__ = [
    "VectorStore",
    "MemoryRecord",
    "KnowledgeGraph",
    "Entity",
    "Relation",
    "HybridRAG",
    "RAGResult",
    "MemoryManager",
    "get_memory_manager",
]
