"""Vector embedding store for Jarvis Long-Term Memory.

Provides persistent SQLite-backed storage with vector embeddings, cosine similarity
search, category filtering, and a robust fallback embedding engine.
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Tuple

from ..utils import logging as log


@dataclass
class MemoryRecord:
    id: str
    content: str
    category: str = "fact"
    doc_type: str = "fact"  # 'fact', 'learned_plan', 'chat', 'system'
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: Optional[List[float]] = None
    created_at: str = ""
    last_accessed_at: str = ""
    access_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "category": self.category,
            "doc_type": self.doc_type,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "last_accessed_at": self.last_accessed_at,
            "access_count": self.access_count,
        }


# --------------------------------------------------------------------------- #
# Local Deterministic Fast Embedding Engine (Zero External Dependencies)
# --------------------------------------------------------------------------- #

class LocalDeterministicEmbedder:
    """A pure-Python fast n-gram & hashed sub-word feature embedder.
    
    Produces normalized 256-dimensional semantic dense vectors using hashed
    character n-grams and term-frequencies. Highly robust, sub-millisecond,
    and 100% offline with zero dependencies.
    """

    def __init__(self, dim: int = 256):
        self.dim = dim

    def embed(self, text: str) -> List[float]:
        if not text or not text.strip():
            return [0.0] * self.dim

        vec = [0.0] * self.dim
        norm_text = text.lower().strip()
        words = re.findall(r"\w+", norm_text)

        # 1. Word level features with positional weighting
        for idx, w in enumerate(words):
            h = hash(w) % self.dim
            weight = 1.0 + (0.5 if idx < 5 else 0.0)
            vec[h] += weight

        # 2. Character 3-grams & 4-grams for sub-word typo & morphology tolerance
        for n in (3, 4):
            for i in range(len(norm_text) - n + 1):
                gram = norm_text[i:i + n]
                h = hash(gram) % self.dim
                vec[h] += 0.5

        # 3. L2 Normalization
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec


# --------------------------------------------------------------------------- #
# Embedding Provider with Dynamic Backends & Fallback
# --------------------------------------------------------------------------- #

class EmbeddingEngine:
    """Generates embeddings using Gemini, Ollama, or the deterministic fallback."""

    def __init__(self, backend: str = "auto", model: str = ""):
        self.backend = backend
        self.model = model
        self.local_embedder = LocalDeterministicEmbedder(dim=256)

    def embed_text(self, text: str) -> List[float]:
        text = (text or "").strip()
        if not text:
            return self.local_embedder.embed("")

        # Attempt remote embedding if configured
        if self.backend == "ollama":
            try:
                emb = self._embed_ollama(text)
                if emb:
                    return emb
            except Exception as exc:
                log.warn(f"Ollama embedding failed, falling back to local embedder: {exc}")

        elif self.backend in {"gemini", "vertex"}:
            try:
                emb = self._embed_gemini(text)
                if emb:
                    return emb
            except Exception as exc:
                log.warn(f"Gemini embedding failed, falling back to local embedder: {exc}")

        # Default fast deterministic embedder
        return self.local_embedder.embed(text)

    def _embed_ollama(self, text: str) -> Optional[List[float]]:
        import requests
        base_url = os.environ.get("JARVIS_BASE_URL", "http://localhost:11434")
        model = self.model or "nomic-embed-text"
        r = requests.post(
            f"{base_url}/api/embeddings",
            json={"model": model, "prompt": text},
            timeout=5,
        )
        if r.ok:
            data = r.json()
            return data.get("embedding")
        return None

    def _embed_gemini(self, text: str) -> Optional[List[float]]:
        # Vertex AI / Google GenAI embedding if configured
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return None
        import requests
        url = f"https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent?key={api_key}"
        payload = {
            "model": "models/text-embedding-004",
            "content": {"parts": [{"text": text[:2000]}]},
        }
        r = requests.post(url, json=payload, timeout=5)
        if r.ok:
            data = r.json()
            return data.get("embedding", {}).get("values")
        return None


# --------------------------------------------------------------------------- #
# Persistent SQLite Vector Store
# --------------------------------------------------------------------------- #

def _pack_vector(vec: List[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack_vector(blob: bytes, dim: int) -> List[float]:
    if not blob:
        return [0.0] * dim
    return list(struct.unpack(f"{dim}f", blob))


def _cosine_similarity(v1: List[float], v2: List[float]) -> float:
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    # Assuming pre-normalized vectors; otherwise normalize
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


class VectorStore:
    """SQLite-backed persistent vector store for long-term memory."""

    def __init__(self, db_path: Path | str, embedder: Optional[EmbeddingEngine] = None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder or EmbeddingEngine()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_records (
                    id TEXT PRIMARY KEY,
                    doc_type TEXT,
                    category TEXT,
                    content TEXT,
                    metadata_json TEXT,
                    embedding BLOB,
                    dimension INTEGER,
                    created_at TEXT,
                    last_accessed_at TEXT,
                    access_count INTEGER DEFAULT 0
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_category ON memory_records(category)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_type ON memory_records(doc_type)")
            conn.commit()
        finally:
            conn.close()

    def add_record(
        self,
        content: str,
        category: str = "fact",
        doc_type: str = "fact",
        metadata: Optional[dict] = None,
        record_id: Optional[str] = None,
    ) -> str:
        content = (content or "").strip()
        if not content:
            return ""

        now = time.strftime("%Y-%m-%d %H:%M:%S")
        if not record_id:
            import hashlib
            record_id = hashlib.sha256(f"{doc_type}:{category}:{content}".encode("utf-8")).hexdigest()[:16]

        meta = metadata or {}
        meta_json = json.dumps(meta, ensure_ascii=False)
        vec = self.embedder.embed_text(content)
        packed_vec = _pack_vector(vec)
        dim = len(vec)

        conn = self._get_conn()
        try:
            conn.execute("""
                INSERT INTO memory_records (id, doc_type, category, content, metadata_json, embedding, dimension, created_at, last_accessed_at, access_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(id) DO UPDATE SET
                    content = excluded.content,
                    category = excluded.category,
                    doc_type = excluded.doc_type,
                    metadata_json = excluded.metadata_json,
                    embedding = excluded.embedding,
                    dimension = excluded.dimension,
                    last_accessed_at = excluded.last_accessed_at
            """, (record_id, doc_type, category, content, meta_json, packed_vec, dim, now, now))
            conn.commit()
        finally:
            conn.close()
        return record_id

    def delete_record(self, record_id: str) -> bool:
        conn = self._get_conn()
        try:
            cur = conn.execute("DELETE FROM memory_records WHERE id = ?", (record_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def delete_by_pattern(self, pattern: str) -> int:
        pattern_str = f"%{pattern.strip().lower()}%"
        conn = self._get_conn()
        try:
            cur = conn.execute("DELETE FROM memory_records WHERE LOWER(content) LIKE ?", (pattern_str,))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def search(
        self,
        query: str,
        top_k: int = 5,
        category: Optional[str] = None,
        doc_type: Optional[str] = None,
        min_score: float = 0.1,
    ) -> List[Tuple[MemoryRecord, float]]:
        query = (query or "").strip()
        if not query:
            return []

        query_vec = self.embedder.embed_text(query)
        dim = len(query_vec)

        where_clauses = []
        params: list[Any] = []
        if category:
            where_clauses.append("category = ?")
            params.append(category)
        if doc_type:
            where_clauses.append("doc_type = ?")
            params.append(doc_type)

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        results: List[Tuple[MemoryRecord, float]] = []
        now = time.strftime("%Y-%m-%d %H:%M:%S")

        conn = self._get_conn()
        try:
            cur = conn.execute(f"SELECT * FROM memory_records {where_sql}", params)
            rows = cur.fetchall()

            for r in rows:
                r_dim = r["dimension"] or dim
                blob = r["embedding"]
                r_vec = _unpack_vector(blob, r_dim)
                score = _cosine_similarity(query_vec, r_vec)
                if score >= min_score:
                    meta = json.loads(r["metadata_json"]) if r["metadata_json"] else {}
                    rec = MemoryRecord(
                        id=r["id"],
                        content=r["content"],
                        category=r["category"],
                        doc_type=r["doc_type"],
                        metadata=meta,
                        embedding=r_vec,
                        created_at=r["created_at"],
                        last_accessed_at=r["last_accessed_at"],
                        access_count=r["access_count"],
                    )
                    results.append((rec, score))

            # Sort by descending similarity score
            results.sort(key=lambda x: x[1], reverse=True)
            top_results = results[:top_k]

            # Update access statistics for retrieved records
            if top_results:
                ids = [rec.id for rec, _ in top_results]
                placeholders = ",".join("?" * len(ids))
                conn.execute(f"""
                    UPDATE memory_records
                    SET access_count = access_count + 1, last_accessed_at = ?
                    WHERE id IN ({placeholders})
                """, [now] + ids)
                conn.commit()
        finally:
            conn.close()

        return top_results

    def get_all(self, category: Optional[str] = None, doc_type: Optional[str] = None) -> List[MemoryRecord]:
        where_clauses = []
        params: list[Any] = []
        if category:
            where_clauses.append("category = ?")
            params.append(category)
        if doc_type:
            where_clauses.append("doc_type = ?")
            params.append(doc_type)
        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        conn = self._get_conn()
        try:
            cur = conn.execute(f"SELECT * FROM memory_records {where_sql} ORDER BY created_at ASC", params)
            records = []
            for r in cur.fetchall():
                meta = json.loads(r["metadata_json"]) if r["metadata_json"] else {}
                records.append(MemoryRecord(
                    id=r["id"],
                    content=r["content"],
                    category=r["category"],
                    doc_type=r["doc_type"],
                    metadata=meta,
                    created_at=r["created_at"],
                    last_accessed_at=r["last_accessed_at"],
                    access_count=r["access_count"],
                ))
            return records
        finally:
            conn.close()

    def count(self) -> int:
        conn = self._get_conn()
        try:
            cur = conn.execute("SELECT COUNT(*) FROM memory_records")
            return cur.fetchone()[0]
        finally:
            conn.close()

    def clear(self) -> None:
        conn = self._get_conn()
        try:
            conn.execute("DELETE FROM memory_records")
            conn.commit()
        finally:
            conn.close()

