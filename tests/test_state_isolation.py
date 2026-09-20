"""Guard the suite's isolation from real, machine-global state.

Every default store Jarvis owns must resolve inside this run's throwaway state
directory, so no test can read or write the developer's real memory store, chat
log, screenshots, browser profile, relay state or live-voice flag. Deleting an
override from ``tests/conftest.py`` - or re-anchoring a path to ``__file__`` -
should fail here on purpose rather than silently reintroduce the leak.
"""

from __future__ import annotations

import inspect
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from jarvis import mcp, sessions
from jarvis.agent import memory as agent_memory
from jarvis.browser_engine import driver
from jarvis.config import Config
from jarvis.daemon.engine import ProactiveDaemon
from jarvis.macro.manager import MacroManager
from jarvis.memory import manager as memory_manager
from jarvis.skills.manager import SkillManager
from jarvis.tools.tool_synthesis import get_default_tools_dir
from jarvis.utils import paths, voice


def _isolated_root() -> Path:
    return Path(os.environ["JARVIS_STATE_DIR"])


def test_the_state_root_override_is_active() -> None:
    assert "JARVIS_STATE_DIR" in os.environ
    assert paths.state_root() == _isolated_root()


def test_the_isolated_root_is_a_throwaway_directory() -> None:
    root = _isolated_root()
    assert root.is_dir()
    assert root != paths.project_root()
    # On Windows the temp tree already lives under the user profile, so the
    # meaningful guarantee is that it is outside the repository, not outside
    # the home directory.
    assert paths.project_root() not in root.parents


def test_both_memory_paths_resolve_inside_the_isolated_root() -> None:
    root = _isolated_root()
    assert memory_manager.get_default_db_path() == root / "jarvis_memory.db"
    assert memory_manager.get_default_memory_path() == root / "memory.txt"
    # The agent package resolves memory.txt separately; they must agree, or the
    # manager's "is this the default?" check quietly stops matching.
    assert agent_memory.get_default_memory_path() == root / "memory.txt"


def test_every_repo_anchored_default_resolves_inside_the_isolated_root() -> None:
    """The seam covers the whole family, not only the stores a test happened to
    touch: a default-constructed store must land in the sandbox too."""
    root = _isolated_root()
    assert sessions.default_sessions_dir() == root / "dataset" / "data" / "sessions"
    assert MacroManager().storage_dir == root / "dataset" / "data" / "macros"
    assert SkillManager().storage_dir == root / "dataset" / "data" / "skills"
    assert get_default_tools_dir() == root / "tools_synthesized"
    assert mcp.get_manager().path == root / "mcp_servers.json"
    # The daemon is reached through a process-global singleton with no rules
    # path (the schedule_task action), so its default is what actually gets
    # written - it must be the sandbox, not the repository's dataset.
    daemon = ProactiveDaemon(cfg=Config(), task_runner=lambda task: None)
    assert daemon.rules_path == root / "dataset" / "data" / "daemon_rules.json"


def test_the_sandbox_survives_a_test_that_clears_the_environment() -> None:
    """Several tests wipe ``os.environ`` (``clear=True``).

    That used to drop every override at once and send default-constructed
    stores back to the repository - the daemon singleton written by the
    ``schedule_task`` action was the one caught doing it.
    """
    root = _isolated_root()
    with patch.dict(os.environ, {}, clear=True):
        assert paths.state_root() == root
        assert paths.browser_profile_dir() == root / "browser_profile"
        assert voice._live_flag_path().parent == root / "live-flag"
        daemon = ProactiveDaemon(cfg=Config(), task_runner=lambda task: None)
        assert daemon.rules_path == root / "dataset" / "data" / "daemon_rules.json"


def test_the_console_cron_store_is_redirected() -> None:
    # Read as text so this guard does not pull the console into the test process.
    source = (
        Path(__file__).resolve().parent.parent / "jarvis" / "console.py"
    ).read_text(encoding="utf-8")
    assert 'state_root() / "cron_jobs.json"' in source


def test_the_browser_profile_is_not_the_users_real_profile() -> None:
    real_profile = Path.home() / ".jarvis" / "browser_profile"
    assert paths.browser_profile_dir() == _isolated_root() / "browser_profile"
    assert paths.browser_profile_dir() != real_profile


def test_the_browser_worker_anchors_screenshots_to_the_state_root() -> None:
    source = inspect.getsource(driver._BrowserWorker.__init__)
    assert "state_root()" in source
    # The original expression anchored screenshots to the repository itself.
    assert 'Path(__file__).resolve().parent.parent.parent / "dataset"' not in source


def test_the_live_voice_flag_is_redirected_by_its_own_override() -> None:
    # The shared flag is what made the latency tests fail whenever a real live
    # session was running on the machine.
    assert voice._live_flag_path().parent == Path(os.environ["JARVIS_LIVE_FLAG_DIR"])
    assert _isolated_root() in voice._live_flag_path().parents


def test_the_live_flag_defaults_to_the_shared_temp_directory(monkeypatch) -> None:
    """With no override and no sandbox, the flag is where it always was."""
    monkeypatch.delenv("JARVIS_LIVE_FLAG_DIR", raising=False)
    previous = paths.sandbox_root()
    paths.set_sandbox_root(None)
    try:
        expected = Path(tempfile.gettempdir()) / "jarvis_live_mode.flag"
        assert voice._live_flag_path() == expected
    finally:
        paths.set_sandbox_root(previous)


def test_no_test_relocates_the_flag_by_patching_a_global() -> None:
    """Relocate the flag with its override, never by patching a temp helper.

    Patching the voice module's temp helper into a private directory mutates the
    standard library module for the whole process, silently dragging unrelated
    temp files (attachments, pasted images, the live-audio probe) along with it.
    """
    # Assembled so this guard does not match its own source text.
    forbidden = "jarvis.utils.voice.tempfile" + ".gettempdir"
    offenders = [
        f"{path.name}:{lineno}"
        for path in sorted(Path(__file__).parent.glob("*.py"))
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if forbidden in line
    ]
    assert not offenders, (
        f"use JARVIS_LIVE_FLAG_DIR instead of patching tempfile: {offenders}"
    )


def test_the_relay_state_file_is_redirected() -> None:
    raw = os.environ.get("RELAY_STATE_PATH", "")
    assert raw, "RELAY_STATE_PATH must be set for the run"
    assert Path(raw).is_absolute()
    assert str(_isolated_root()) in raw
