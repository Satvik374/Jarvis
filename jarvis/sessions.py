"""Session management for Jarvis.

Provides automatic saving of conversation sessions, AI-generated session titles,
session switching, loading, and persistence.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from .config import ROOT
from .utils import logging as log


def default_sessions_dir() -> Path:
    d = ROOT / "dataset" / "data" / "sessions"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


class Session:
    def __init__(
        self,
        session_id: str | None = None,
        title: str = "New Session",
        created_at: float | None = None,
        updated_at: float | None = None,
        messages: list[dict[str, Any]] | None = None,
        has_ai_title: bool = False,
    ):
        self.id = session_id or f"session_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        self.title = title
        self.created_at = created_at or time.time()
        self.updated_at = updated_at or self.created_at
        self.messages = list(messages or [])
        self.has_ai_title = has_ai_title

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "messages": self.messages,
            "has_ai_title": self.has_ai_title,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        return cls(
            session_id=data.get("id"),
            title=data.get("title", "New Session"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            messages=data.get("messages"),
            has_ai_title=data.get("has_ai_title", False),
        )


class SessionManager:
    """Manages persistent sessions on disk."""

    def __init__(self, sessions_dir: Path | None = None):
        self.sessions_dir = sessions_dir or default_sessions_dir()
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.active_session_id: str | None = None
        self._ensure_initial_session()

    def _path_for(self, session_id: str) -> Path:
        # Sanitize session_id to prevent directory traversal
        clean_id = os.path.basename(session_id).replace("..", "")
        return self.sessions_dir / f"{clean_id}.json"

    def _ensure_initial_session(self) -> None:
        sessions = self.list_sessions()
        if sessions:
            self.active_session_id = sessions[0]["id"]
        else:
            session = self.create_session("New Session")
            self.active_session_id = session.id

    def create_session(self, title: str = "New Session") -> Session:
        session = Session(title=title)
        self.save_session(session)
        self.active_session_id = session.id
        return session

    def save_session(self, session: Session) -> None:
        session.updated_at = time.time()
        path = self._path_for(session.id)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(session.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception as exc:
            log.warn(f"Failed to save session {session.id}: {exc}")

    def load_session(self, session_id: str) -> Session | None:
        path = self._path_for(session_id)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return Session.from_dict(data)
        except Exception as exc:
            log.warn(f"Failed to load session {session_id}: {exc}")
            return None

    def get_active_session(self) -> Session:
        if self.active_session_id:
            s = self.load_session(self.active_session_id)
            if s is not None:
                return s
        return self.create_session("New Session")

    def list_sessions(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not self.sessions_dir.exists():
            return out
        for f in self.sessions_dir.glob("*.json"):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                out.append({
                    "id": data.get("id", f.stem),
                    "title": data.get("title", "New Session"),
                    "created_at": data.get("created_at", 0),
                    "updated_at": data.get("updated_at", 0),
                    "message_count": len(data.get("messages", [])),
                    "has_ai_title": data.get("has_ai_title", False),
                })
            except Exception:
                pass
        out.sort(key=lambda x: x.get("updated_at", 0), reverse=True)
        return out

    def delete_session(self, session_id: str) -> bool:
        path = self._path_for(session_id)
        if not path.exists():
            return False
        try:
            path.unlink()
            if self.active_session_id == session_id:
                sessions = self.list_sessions()
                if sessions:
                    self.active_session_id = sessions[0]["id"]
                else:
                    new_s = self.create_session("New Session")
                    self.active_session_id = new_s.id
            return True
        except Exception as exc:
            log.warn(f"Failed to delete session {session_id}: {exc}")
            return False

    def append_message(
        self,
        role: str,
        content: str,
        session_id: str | None = None,
        display_text: str | None = None,
    ) -> Session:
        sid = session_id or self.active_session_id
        session = (self.load_session(sid) if sid else None) or self.get_active_session()
        msg: dict[str, Any] = {
            "role": role,
            "content": content,
            "timestamp": time.time(),
        }
        if display_text and display_text != content:
            msg["display_text"] = display_text
        session.messages.append(msg)
        self.save_session(session)
        return session

    def generate_ai_title(self, session: Session, brain: Any) -> str | None:
        """Ask the brain for a concise 3-6 word summary title for the session."""
        latest = self.load_session(session.id) or session
        if latest.has_ai_title or not latest.messages:
            return None
        # Find first user directive
        user_msgs = [m for m in latest.messages if m.get("role") == "user"]
        if not user_msgs:
            return None
        first_directive = user_msgs[0].get("content", "")[:300]
        if not first_directive.strip():
            return None


        system = (
            "You are a title generator for conversation sessions. "
            "Generate a concise, catchy, 3 to 6 word title summarizing the directive. "
            "Return ONLY the plain title without quotes, punctuation or extra text."
        )
        prompt = f"Directive: {first_directive}"
        try:
            if hasattr(brain, "complete"):
                raw = brain.complete(system, [{"role": "user", "content": prompt}])
            else:
                raw = ""
            clean_title = raw.strip().strip('"').strip("'").splitlines()[0][:50]
            if clean_title:
                session.title = clean_title
                session.has_ai_title = True
                latest.title = clean_title
                latest.has_ai_title = True
                self.save_session(latest)
                return clean_title
        except Exception as exc:
            log.warn(f"Failed to generate AI session title: {exc}")
        return None



_SINGLETON: SessionManager | None = None


def get_session_manager() -> SessionManager:
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = SessionManager()
    return _SINGLETON
