import pytest
from pathlib import Path
import tempfile
import time

from jarvis.memory.manager import MemoryManager
from jarvis.daemon.watchers import ClipboardWatcher
from jarvis.daemon.events import EventType


def test_extract_and_remember_preferences():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        mem_file = Path(tmpdir) / "memory.txt"
        mgr = MemoryManager(db_path=db_path, memory_path=mem_file, embedding_backend="dummy")

        text = "Hello Jarvis, my name is Alex and I prefer dark theme for all apps."
        extracted = mgr.extract_and_remember(text)

        assert len(extracted) >= 1
        all_facts = mgr.vector_store.get_all(doc_type="fact")
        assert any("dark theme" in f.content.lower() or "alex" in f.content.lower() for f in all_facts)


def test_clipboard_watcher_url(monkeypatch):
    watcher = ClipboardWatcher()
    monkeypatch.setattr("pyperclip.paste", lambda: "https://github.com/google/gemini")

    # Initial check sets baseline
    events = watcher.check()
    assert len(events) == 0

    # Clipboard change
    monkeypatch.setattr("pyperclip.paste", lambda: "https://python.org")
    events = watcher.check()
    assert len(events) == 1
    assert events[0].type == EventType.CLIPBOARD_SUGGESTION
    assert "URL Copied" in events[0].title


def test_clipboard_watcher_error(monkeypatch):
    watcher = ClipboardWatcher()
    monkeypatch.setattr("pyperclip.paste", lambda: "normal text")
    watcher.check()

    error_text = "Traceback (most recent call last):\n  File 'test.py', line 1\nSyntaxError: invalid syntax"
    monkeypatch.setattr("pyperclip.paste", lambda: error_text)
    events = watcher.check()
    assert len(events) == 1
    assert events[0].type == EventType.CLIPBOARD_SUGGESTION
    assert "Error Traceback" in events[0].title
