"""Tests for automatic session manager and AI title generation."""

import json
from pathlib import Path
import pytest
from jarvis.sessions import Session, SessionManager


@pytest.fixture
def temp_sessions_dir(tmp_path):
    d = tmp_path / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_create_and_load_session(temp_sessions_dir):
    sm = SessionManager(sessions_dir=temp_sessions_dir)
    session = sm.create_session(title="Test Session")
    assert session.title == "Test Session"
    assert sm.active_session_id == session.id

    loaded = sm.load_session(session.id)
    assert loaded is not None
    assert loaded.id == session.id
    assert loaded.title == "Test Session"


def test_append_message_saves_to_disk(temp_sessions_dir):
    sm = SessionManager(sessions_dir=temp_sessions_dir)
    session = sm.get_active_session()
    sm.append_message("user", "Hello Jarvis", session_id=session.id)
    sm.append_message("assistant", "Greetings!", session_id=session.id)

    loaded = sm.load_session(session.id)
    assert loaded is not None
    assert len(loaded.messages) == 2
    assert loaded.messages[0]["role"] == "user"
    assert loaded.messages[0]["content"] == "Hello Jarvis"
    assert loaded.messages[1]["role"] == "assistant"
    assert loaded.messages[1]["content"] == "Greetings!"


def test_list_sessions_ordered_by_updated_at(temp_sessions_dir):
    sm = SessionManager(sessions_dir=temp_sessions_dir)
    s1 = sm.create_session("First Session")
    sm.append_message("user", "Directive 1", session_id=s1.id)

    s2 = sm.create_session("Second Session")
    sm.append_message("user", "Directive 2", session_id=s2.id)

    lst = sm.list_sessions()
    assert len(lst) >= 2
    assert lst[0]["id"] == s2.id
    assert lst[0]["title"] == "Second Session"


def test_delete_session(temp_sessions_dir):
    sm = SessionManager(sessions_dir=temp_sessions_dir)
    s1 = sm.create_session("ToDelete")
    assert sm.load_session(s1.id) is not None

    deleted = sm.delete_session(s1.id)
    assert deleted is True
    assert sm.load_session(s1.id) is None


def test_generate_ai_title(temp_sessions_dir):
    sm = SessionManager(sessions_dir=temp_sessions_dir)
    session = sm.create_session("New Session")
    sm.append_message("user", "Build a python script that scrapes stock data", session_id=session.id)
    sm.append_message("assistant", "I will write a stock scraper script for you.", session_id=session.id)

    class DummyBrain:
        def complete(self, system, messages):
            return "Stock Scraper Script"

    title = sm.generate_ai_title(session, DummyBrain())
    assert title == "Stock Scraper Script"
    assert session.title == "Stock Scraper Script"
    assert session.has_ai_title is True

    loaded = sm.load_session(session.id)
    assert loaded.title == "Stock Scraper Script"
    assert loaded.has_ai_title is True
