"""Knowledge Graph Engine for Jarvis Long-Term Memory.

Stores entities, attributes, and directed relationships (Subject -> Predicate -> Object).
Provides subgraph querying (k-hop expansion), entity extraction, and relationship lookups.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ..utils import logging as log


@dataclass
class Entity:
    id: str
    name: str
    entity_type: str = "concept"  # 'person', 'app', 'project', 'preference', 'device', 'file', 'concept'
    aliases: List[str] = field(default_factory=list)
    summary: str = ""
    attributes: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "entity_type": self.entity_type,
            "aliases": self.aliases,
            "summary": self.summary,
            "attributes": self.attributes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class Relation:
    id: str
    source_id: str
    source_name: str
    target_id: str
    target_name: str
    relation_type: str  # 'prefers', 'owns', 'friend_of', 'located_at', 'uses', 'has_playlist', 'controls', 'rule_for'
    weight: float = 1.0
    context: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source_name": self.source_name,
            "relation_type": self.relation_type,
            "target_name": self.target_name,
            "weight": self.weight,
            "context": self.context,
        }


class KnowledgeGraph:
    """SQLite-backed Knowledge Graph for entity-relation modeling."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS entities (
                    id TEXT PRIMARY KEY,
                    name TEXT UNIQUE COLLATE NOCASE,
                    entity_type TEXT,
                    aliases_json TEXT,
                    summary TEXT,
                    attributes_json TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_entity_name ON entities(name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_entity_type ON entities(entity_type)")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS relations (
                    id TEXT PRIMARY KEY,
                    source_id TEXT,
                    target_id TEXT,
                    relation_type TEXT,
                    weight REAL DEFAULT 1.0,
                    context TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    FOREIGN KEY(source_id) REFERENCES entities(id) ON DELETE CASCADE,
                    FOREIGN KEY(target_id) REFERENCES entities(id) ON DELETE CASCADE,
                    UNIQUE(source_id, target_id, relation_type)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rel_source ON relations(source_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rel_target ON relations(target_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rel_type ON relations(relation_type)")
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------ #
    # Entity CRUD
    # ------------------------------------------------------------------ #

    def add_or_update_entity(
        self,
        name: str,
        entity_type: str = "concept",
        summary: str = "",
        aliases: Optional[List[str]] = None,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> Entity:
        name = name.strip()
        if not name:
            raise ValueError("Entity name cannot be empty")

        now = time.strftime("%Y-%m-%d %H:%M:%S")
        import hashlib
        entity_id = hashlib.sha256(name.lower().encode("utf-8")).hexdigest()[:16]

        existing = self.get_entity(name)
        merged_aliases = set(aliases or [])
        merged_attributes = attributes or {}
        merged_summary = summary or ""

        if existing:
            merged_aliases.update(existing.aliases)
            old_attrs = dict(existing.attributes)
            old_attrs.update(merged_attributes)
            merged_attributes = old_attrs
            if not merged_summary:
                merged_summary = existing.summary
            entity_id = existing.id

        aliases_json = json.dumps(sorted(list(merged_aliases)), ensure_ascii=False)
        attrs_json = json.dumps(merged_attributes, ensure_ascii=False)

        conn = self._get_conn()
        try:
            conn.execute("""
                INSERT INTO entities (id, name, entity_type, aliases_json, summary, attributes_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    entity_type = excluded.entity_type,
                    aliases_json = excluded.aliases_json,
                    summary = CASE WHEN excluded.summary != '' THEN excluded.summary ELSE entities.summary END,
                    attributes_json = excluded.attributes_json,
                    updated_at = excluded.updated_at
            """, (entity_id, name, entity_type, aliases_json, merged_summary, attrs_json, now, now))
            conn.commit()
        finally:
            conn.close()

        return Entity(
            id=entity_id,
            name=name,
            entity_type=entity_type,
            aliases=sorted(list(merged_aliases)),
            summary=merged_summary,
            attributes=merged_attributes,
            created_at=now,
            updated_at=now,
        )

    def get_entity(self, name_or_alias: str) -> Optional[Entity]:
        key = name_or_alias.strip().lower()
        if not key:
            return None

        conn = self._get_conn()
        try:
            # 1. Exact match on name
            cur = conn.execute("SELECT * FROM entities WHERE LOWER(name) = ?", (key,))
            row = cur.fetchone()
            if row:
                return self._row_to_entity(row)

            # 2. Check aliases
            cur = conn.execute("SELECT * FROM entities")
            for r in cur.fetchall():
                aliases = json.loads(r["aliases_json"]) if r["aliases_json"] else []
                if any(a.lower() == key for a in aliases):
                    return self._row_to_entity(r)
            return None
        finally:
            conn.close()

    def _row_to_entity(self, row: sqlite3.Row) -> Entity:
        aliases = json.loads(row["aliases_json"]) if row["aliases_json"] else []
        attrs = json.loads(row["attributes_json"]) if row["attributes_json"] else {}
        return Entity(
            id=row["id"],
            name=row["name"],
            entity_type=row["entity_type"],
            aliases=aliases,
            summary=row["summary"] or "",
            attributes=attrs,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ------------------------------------------------------------------ #
    # Relations CRUD
    # ------------------------------------------------------------------ #

    def add_relation(
        self,
        source_name: str,
        relation_type: str,
        target_name: str,
        context: str = "",
        weight: float = 1.0,
        source_type: str = "concept",
        target_type: str = "concept",
    ) -> Relation:
        src = self.add_or_update_entity(source_name, entity_type=source_type)
        tgt = self.add_or_update_entity(target_name, entity_type=target_type)

        rel_type = relation_type.strip().lower().replace(" ", "_")
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        import hashlib
        rel_id = hashlib.sha256(f"{src.id}:{rel_type}:{tgt.id}".encode("utf-8")).hexdigest()[:16]

        conn = self._get_conn()
        try:
            conn.execute("""
                INSERT INTO relations (id, source_id, target_id, relation_type, weight, context, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, target_id, relation_type) DO UPDATE SET
                    weight = excluded.weight,
                    context = CASE WHEN excluded.context != '' THEN excluded.context ELSE relations.context END,
                    updated_at = excluded.updated_at
            """, (rel_id, src.id, tgt.id, rel_type, weight, context, now, now))
            conn.commit()
        finally:
            conn.close()

        return Relation(
            id=rel_id,
            source_id=src.id,
            source_name=src.name,
            target_id=tgt.id,
            target_name=tgt.name,
            relation_type=rel_type,
            weight=weight,
            context=context,
            created_at=now,
            updated_at=now,
        )

    def get_relations_for_entity(self, entity_name: str) -> Tuple[List[Relation], List[Relation]]:
        """Returns (outgoing_relations, incoming_relations) for an entity."""
        ent = self.get_entity(entity_name)
        if not ent:
            return [], []

        outgoing: List[Relation] = []
        incoming: List[Relation] = []

        conn = self._get_conn()
        try:
            # Outgoing: ent -> target
            cur = conn.execute("""
                SELECT r.*, s.name as s_name, t.name as t_name
                FROM relations r
                JOIN entities s ON r.source_id = s.id
                JOIN entities t ON r.target_id = t.id
                WHERE r.source_id = ?
            """, (ent.id,))
            for row in cur.fetchall():
                outgoing.append(Relation(
                    id=row["id"],
                    source_id=row["source_id"],
                    source_name=row["s_name"],
                    target_id=row["target_id"],
                    target_name=row["t_name"],
                    relation_type=row["relation_type"],
                    weight=row["weight"],
                    context=row["context"] or "",
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                ))

            # Incoming: source -> ent
            cur = conn.execute("""
                SELECT r.*, s.name as s_name, t.name as t_name
                FROM relations r
                JOIN entities s ON r.source_id = s.id
                JOIN entities t ON r.target_id = t.id
                WHERE r.target_id = ?
            """, (ent.id,))
            for row in cur.fetchall():
                incoming.append(Relation(
                    id=row["id"],
                    source_id=row["source_id"],
                    source_name=row["s_name"],
                    target_id=row["target_id"],
                    target_name=row["t_name"],
                    relation_type=row["relation_type"],
                    weight=row["weight"],
                    context=row["context"] or "",
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                ))
        finally:
            conn.close()

        return outgoing, incoming

    def query_subgraph(self, entity_names: List[str], max_hops: int = 1) -> Dict[str, Any]:
        """Traverse the graph from seed entities up to max_hops to retrieve connected nodes & edges."""
        visited_entity_ids: Set[str] = set()
        entities_dict: Dict[str, Entity] = {}
        relations_list: List[Relation] = []

        queue = []
        for name in entity_names:
            ent = self.get_entity(name)
            if ent and ent.id not in visited_entity_ids:
                visited_entity_ids.add(ent.id)
                entities_dict[ent.id] = ent
                queue.append((ent, 0))

        while queue:
            curr_ent, hop = queue.pop(0)
            if hop >= max_hops:
                continue

            out_rels, in_rels = self.get_relations_for_entity(curr_ent.name)
            for r in out_rels + in_rels:
                if r not in relations_list:
                    relations_list.append(r)

                neighbor_name = r.target_name if r.source_id == curr_ent.id else r.source_name
                neighbor_ent = self.get_entity(neighbor_name)
                if neighbor_ent and neighbor_ent.id not in visited_entity_ids:
                    visited_entity_ids.add(neighbor_ent.id)
                    entities_dict[neighbor_ent.id] = neighbor_ent
                    queue.append((neighbor_ent, hop + 1))

        return {
            "entities": list(entities_dict.values()),
            "relations": relations_list,
        }

    # ------------------------------------------------------------------ #
    # Rule-Based Entity & Relation Extraction from Natural Language
    # ------------------------------------------------------------------ #

    def extract_and_index(self, text: str, category: str = "fact") -> List[Relation]:
        """Extracts entities and relationships from natural language text and indexes them."""
        text = (text or "").strip()
        if not text:
            return []

        extracted_relations: List[Relation] = []

        # 1. Explicit triplet pattern: "[Subject] -> [predicate] -> [Object]" or "Subject -predicate-> Object"
        arrow_matches = re.findall(r"\[?([\w\s\.-]+)\]?\s*(?:->|-{1,2}>)\s*\[?([\w\s\.-]+)\]?\s*(?:->|-{1,2}>)\s*\[?([\w\s\.:\\/-]+)\]?", text)
        for s, p, o in arrow_matches:
            s, p, o = s.strip(), p.strip(), o.strip()
            if s and p and o:
                rel = self.add_relation(s, p, o, context=text)
                extracted_relations.append(rel)

        # 2. Preference pattern: "User prefers X over Y" / "User prefers X"
        pref_match = re.search(r"(?:user|i)\s+prefers?\s+([^.,;\n]+)", text, re.IGNORECASE)
        if pref_match:
            pref_val = pref_match.group(1).strip()
            rel = self.add_relation("User", "prefers", pref_val, context=text, source_type="person", target_type="preference")
            extracted_relations.append(rel)

        # 3. Spotify / App playlist pattern: "open Spotify ... play their 'X' playlist"
        spot_match = re.search(r"spotify.*?['\"]([^'\"]+)['\"]\s+playlist", text, re.IGNORECASE)
        if spot_match:
            playlist = spot_match.group(1).strip()
            self.add_or_update_entity("Spotify", entity_type="app")
            self.add_or_update_entity(playlist, entity_type="playlist")
            rel1 = self.add_relation("User", "prefers_playlist", playlist, context=text, source_type="person", target_type="playlist")
            rel2 = self.add_relation("Spotify", "has_playlist", playlist, context=text, source_type="app", target_type="playlist")
            extracted_relations.extend([rel1, rel2])

        # 4. Project & Repo path pattern: "Own Code dir is X and Git repo is Y"
        code_dir_match = re.search(r"code dir is\s+([A-Za-z]:\\[^,;\n\s]+|/[^,;\n\s]+)", text, re.IGNORECASE)
        repo_match = re.search(r"(?:git repo|repo) is\s+(https?://[^\s]+|\S+\.git)", text, re.IGNORECASE)
        if code_dir_match or repo_match:
            self.add_or_update_entity("Jarvis", entity_type="project")
            if code_dir_match:
                path = code_dir_match.group(1).strip()
                rel = self.add_relation("Jarvis", "located_at", path, context=text, source_type="project", target_type="file_path")
                extracted_relations.append(rel)
            if repo_match:
                repo = repo_match.group(1).strip()
                rel = self.add_relation("Jarvis", "has_git_repo", repo, context=text, source_type="project", target_type="url")
                extracted_relations.append(rel)

        # 5. Social / Person relation pattern: "friend X" / "colleague X" / "contact X"
        person_match = re.search(r"(?:friend|colleague|contact|brother|sister|boss)\s+([A-Z][a-z]+)", text)
        if person_match:
            person_name = person_match.group(1).strip()
            rel = self.add_relation(person_name, "friend_of", "User", context=text, source_type="person", target_type="person")
            extracted_relations.append(rel)

        # 6. Device specific rules: "If a task is specified for Mobile..."
        if "mobile" in text.lower():
            self.add_or_update_entity("Mobile", entity_type="device")
            rel = self.add_relation("Jarvis", "has_device_rule", text[:100], context=text, source_type="system", target_type="rule")
            extracted_relations.append(rel)

        # 7. Generic "[category] Subject ... Object"
        if not extracted_relations and category:
            self.add_or_update_entity("User", entity_type="person")
            summary_snippet = text[:80]
            rel = self.add_relation("User", f"has_{category}", summary_snippet, context=text)
            extracted_relations.append(rel)

        return extracted_relations

    def find_mentioned_entities(self, text: str) -> List[Entity]:
        """Finds any registered entities or aliases mentioned in the provided text."""
        text_lower = f" {text.lower()} "
        found: List[Entity] = []

        conn = self._get_conn()
        try:
            cur = conn.execute("SELECT * FROM entities")
            for r in cur.fetchall():
                name = r["name"].lower()
                if re.search(r"\b" + re.escape(name) + r"\b", text_lower):
                    found.append(self._row_to_entity(r))
                    continue

                aliases = json.loads(r["aliases_json"]) if r["aliases_json"] else []
                for alias in aliases:
                    if re.search(r"\b" + re.escape(alias.lower()) + r"\b", text_lower):
                        found.append(self._row_to_entity(r))
                        break
            return found
        finally:
            conn.close()

    def get_all_triplets(self) -> List[Tuple[str, str, str, str]]:
        """Returns list of (source_name, relation_type, target_name, context)."""
        conn = self._get_conn()
        try:
            cur = conn.execute("""
                SELECT s.name as s_name, r.relation_type, t.name as t_name, r.context
                FROM relations r
                JOIN entities s ON r.source_id = s.id
                JOIN entities t ON r.target_id = t.id
                ORDER BY s.name ASC, r.relation_type ASC
            """)
            return [(r["s_name"], r["relation_type"], r["t_name"], r["context"] or "") for r in cur.fetchall()]
        finally:
            conn.close()

    def count_stats(self) -> Dict[str, int]:
        conn = self._get_conn()
        try:
            e_count = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
            r_count = conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
            return {"entities": e_count, "relations": r_count}
        finally:
            conn.close()

    def clear(self) -> None:
        conn = self._get_conn()
        try:
            conn.execute("DELETE FROM relations")
            conn.execute("DELETE FROM entities")
            conn.commit()
        finally:
            conn.close()
