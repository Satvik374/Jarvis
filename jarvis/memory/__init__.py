"""Long-Term Memory Subsystem for JARVIS.

Combines Vector Semantic Search (RAG) + Knowledge Graph (Entity-Relations)
+ Keyword/BM25 Matching into a unified, token-efficient long-term memory engine.
"""

from .vector_store import VectorStore, MemoryRecord
from .knowledge_graph import KnowledgeGraph, Entity, Relation
from .hybrid_rag import HybridRAG, RAGResult
from .manager import MemoryManager, get_memory_manager
from .obsidian import (
    Note,
    ObsidianVault,
    VaultUnavailable,
    get_vault,
    vault_path_setting,
)
from .coordinates import (
    Coordinate,
    CoordinateStore,
    default_store_path as coordinates_path,
    find as find_coordinates,
    forget as forget_coordinates,
    get_store as get_coordinate_store,
    note as coordinates_note,
    save as save_coordinate,
)

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
    # Obsidian vault as a memory source and sink - see jarvis/memory/obsidian.py.
    "Note",
    "ObsidianVault",
    "VaultUnavailable",
    "get_vault",
    "vault_path_setting",
    # Named click targets (pc / mobile) - see jarvis/memory/coordinates.py.
    "Coordinate",
    "CoordinateStore",
    "coordinates_path",
    "find_coordinates",
    "forget_coordinates",
    "get_coordinate_store",
    "coordinates_note",
    "save_coordinate",
]
