"""One owner for where Jarvis keeps state.

``jarvis/utils/paths.py`` is the single authority: every store, and every action
handler that resolves a default location, must agree with it - with an override
set and with none. These tests drive the real handlers instead of reading source
text, because the failure they guard against was two call sites disagreeing
about the same file, which a source check cannot see.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

import pytest

from jarvis.agent import memory as agent_memory
from jarvis.agent import subagent
from jarvis.auth import codex_oauth
from jarvis.config import ROOT, Config
from jarvis.daemon import set_daemon
from jarvis.memory import manager as memory_manager
from jarvis.security.vault import CredentialVault
from jarvis.tools import registry
from jarvis.utils import paths


@pytest.fixture
def state_dir(monkeypatch):
    """Point the whole state root at a private directory for one test.

    The browser profile has its own override, and a per-location override beats
    the state root by design, so both are moved here.
    """
    with tempfile.TemporaryDirectory(prefix="jarvis-seam-") as tmp:
        monkeypatch.setenv("JARVIS_STATE_DIR", tmp)
        monkeypatch.setenv("JARVIS_BROWSER_PROFILE", str(Path(tmp) / "browser_profile"))
        yield Path(tmp)


@pytest.fixture
def fresh_daemon():
    """The daemon is a process-global singleton; start and leave it empty."""
    set_daemon(None)
    yield
    set_daemon(None)


def _fingerprint(path: Path):
    if not path.exists():
        return None
    stat = path.stat()
    return (stat.st_size, stat.st_mtime_ns)


@contextlib.contextmanager
def _restore_on_regression(*paths_to_watch: Path):
    """Put the real files back if the seam turns out to be broken.

    These tests drive the destructive handlers at memory.txt and the daemon
    rules on purpose, so if a future edit sends them back to the source tree
    the write has already happened by the time the assertion fails. The test
    must not be what costs a developer their memory.
    """
    saved = {path: (path.read_bytes() if path.exists() else None) for path in paths_to_watch}
    try:
        yield
    finally:
        for path, original in saved.items():
            now = path.read_bytes() if path.exists() else None
            if now == original:
                continue
            if original is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(original)


def _run(action: str, args: dict):
    return registry.execute(action, args, None, Config())


def test_read_file_hands_the_model_the_memory_the_manager_writes(state_dir):
    """The bug: read_file resolved memory.txt from the source tree while the
    memory manager resolved it from the state root, so the model was shown a
    different file than the one Jarvis maintains."""
    expected = "# override memory\nI am the agent's memory, under the state root.\n"
    (state_dir / "memory.txt").write_text(expected, encoding="utf-8")

    result = _run("read_file", {"path": "memory.txt"})

    assert result.ok
    assert result.message == expected


def test_write_file_memory_txt_lands_under_the_state_root(state_dir):
    repo_memory = paths.project_root() / "memory.txt"
    before = _fingerprint(repo_memory)

    with _restore_on_regression(repo_memory):
        result = _run("write_file", {"path": "memory.txt",
                                     "content": "learned something\n"})
        assert result.ok
        assert (state_dir / "memory.txt").read_text(
            encoding="utf-8") == "learned something\n"
        assert _fingerprint(repo_memory) == before


def test_the_daemon_rule_action_writes_rules_under_the_state_root(state_dir, fresh_daemon):
    repo_rules = paths.project_root() / "dataset" / "data" / "daemon_rules.json"
    before = _fingerprint(repo_rules)

    with _restore_on_regression(repo_rules):
        result = _run("daemon_rule", {
            "action": "add",
            "name": "Seam Probe",
            "trigger": "custom",
            "action_type": "notify",
            "target": "hello",
        })
        assert result.ok, result.message
        sandbox_rules = state_dir / "dataset" / "data" / "daemon_rules.json"
        assert sandbox_rules.exists()
        assert "Seam Probe" in sandbox_rules.read_text(encoding="utf-8")
        assert _fingerprint(repo_rules) == before


def test_every_store_default_resolves_under_the_same_root(state_dir):
    assert paths.state_root() == state_dir
    assert memory_manager.get_default_memory_path() == state_dir / "memory.txt"
    assert agent_memory.get_default_memory_path() == state_dir / "memory.txt"
    assert memory_manager.get_default_db_path() == state_dir / "jarvis_memory.db"
    assert paths.browser_profile_dir() == state_dir / "browser_profile"
    assert CredentialVault().vault_file == state_dir / "dataset" / "data" / "vault.enc"
    assert codex_oauth.fallback_auth_path() == state_dir / ".codex_tokens.json"


def test_the_project_root_has_one_definition():
    assert ROOT == paths.project_root()
    assert subagent._project_root() == paths.project_root()


def test_the_documented_precedence_is_the_observed_one():
    """paths.py documents: override, then sandbox, then the historical default.

    Managed by hand rather than by ``monkeypatch`` so the assertions below can
    prove this test put the run's own isolation back.
    """
    run_root = os.environ.get("JARVIS_STATE_DIR")
    previous_box = paths.sandbox_root()
    try:
        with tempfile.TemporaryDirectory() as override, tempfile.TemporaryDirectory() as box:
            paths.set_sandbox_root(box)
            os.environ["JARVIS_STATE_DIR"] = override
            assert paths.state_root() == Path(override)      # 1. override wins
            os.environ.pop("JARVIS_STATE_DIR")
            assert paths.state_root() == Path(box)           # 2. sandbox survives
    finally:
        if run_root is None:
            os.environ.pop("JARVIS_STATE_DIR", None)
        else:
            os.environ["JARVIS_STATE_DIR"] = run_root
        paths.set_sandbox_root(previous_box)
    assert os.environ.get("JARVIS_STATE_DIR") == run_root
    assert paths.sandbox_root() == previous_box


def test_with_no_override_every_location_is_where_it_always_was(monkeypatch):
    monkeypatch.delenv("JARVIS_STATE_DIR", raising=False)
    monkeypatch.delenv("JARVIS_BROWSER_PROFILE", raising=False)
    previous = paths.sandbox_root()
    paths.set_sandbox_root(None)
    try:
        root = paths.project_root()
        assert paths.state_root() == root
        assert memory_manager.get_default_memory_path() == root / "memory.txt"
        assert memory_manager.get_default_db_path() == root / "jarvis_memory.db"
        assert paths.browser_profile_dir() == Path.home() / ".jarvis" / "browser_profile"
        assert CredentialVault().vault_file == root / "dataset" / "data" / "vault.enc"
        assert codex_oauth.fallback_auth_path() == root / ".codex_tokens.json"
        assert ROOT == root
    finally:
        paths.set_sandbox_root(previous)
