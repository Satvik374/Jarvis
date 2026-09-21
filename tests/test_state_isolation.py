"""Guard the suite's isolation from real, machine-global state.

Every default store Jarvis owns must resolve inside this run's throwaway state
directory, so no test can read or write the developer's real memory store, chat
log, screenshots, browser profile, relay state or live-voice flag. Deleting an
override from ``tests/conftest.py`` - or re-anchoring a path to ``__file__`` -
should fail here on purpose rather than silently reintroduce the leak.

Each location is asserted by calling the code that resolves it, so re-anchoring
a path fails these tests without an assertion holding a copy of the source.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from jarvis import console, mcp, sessions
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


def _daemon() -> ProactiveDaemon:
    return ProactiveDaemon(cfg=Config(), task_runner=lambda task: None)


def test_the_isolated_root_is_not_the_repository() -> None:
    """The sandbox has to be somewhere else, or the rest of this is moot."""
    root = _isolated_root()
    assert root != paths.project_root()
    # On Windows the temp tree already lives under the user profile, so the
    # meaningful guarantee is that it is outside the repository, not outside
    # the home directory.
    assert paths.project_root() not in root.parents


def test_every_store_default_resolves_inside_the_isolated_root() -> None:
    """The whole family, not only the stores a test happened to touch."""
    root = _isolated_root()
    # The agent package resolves memory.txt separately from the memory manager;
    # they must agree, or the manager's "is this the default?" check quietly
    # stops matching.
    assert memory_manager.get_default_memory_path() == root / "memory.txt"
    assert agent_memory.get_default_memory_path() == root / "memory.txt"
    assert memory_manager.get_default_db_path() == root / "jarvis_memory.db"
    assert paths.browser_profile_dir() == root / "browser_profile"
    assert sessions.default_sessions_dir() == root / "dataset" / "data" / "sessions"
    assert MacroManager().storage_dir == root / "dataset" / "data" / "macros"
    assert SkillManager().storage_dir == root / "dataset" / "data" / "skills"
    assert get_default_tools_dir() == root / "tools_synthesized"
    assert mcp.get_manager().path == root / "mcp_servers.json"
    assert console.cron_store_path() == root / "cron_jobs.json"
    assert driver.screenshot_dir() == root / "dataset" / "data" / "screenshots"
    # The daemon is reached through a process-global singleton with no rules
    # path (the schedule_task action), so its default is what actually gets
    # written - it must be the sandbox, not the repository's dataset.
    assert _daemon().rules_path == root / "dataset" / "data" / "daemon_rules.json"


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
        assert paths.live_flag_path().parent == root / "live-flag"
        assert _daemon().rules_path == root / "dataset" / "data" / "daemon_rules.json"


def test_live_mode_writes_the_flag_inside_the_isolated_root() -> None:
    """The flag a live session heartbeats is the one the latency tests stumbled on.

    Driven through the live-mode entry point rather than by reading a path, so
    this covers the writer as well as the resolution.
    """
    voice.set_live_mode_active(True)
    try:
        flag = paths.live_flag_path()
        assert flag.exists()
        assert _isolated_root() in flag.parents
    finally:
        voice.set_live_mode_active(False)
    assert not paths.live_flag_path().exists()


def test_the_live_flag_defaults_to_the_shared_temp_directory(monkeypatch) -> None:
    """With no override and no sandbox, the flag is where it always was."""
    monkeypatch.delenv("JARVIS_LIVE_FLAG_DIR", raising=False)
    previous = paths.sandbox_root()
    paths.set_sandbox_root(None)
    try:
        assert paths.live_flag_path() == Path(tempfile.gettempdir()) / "jarvis_live_mode.flag"
    finally:
        paths.set_sandbox_root(previous)


def test_no_test_relocates_state_by_patching_a_stdlib_helper() -> None:
    """Relocate a location with its override, never by patching a temp helper.

    This one stays a source check on purpose: the property is about the suite's
    own files, so there is nothing to run to observe it. Patching the temp
    helper used to drag unrelated temp files (attachments, pasted images, the
    live-audio probe) along with the flag, which is how a real leak hid.
    """
    forbidden = [
        "jarvis.utils.voice.tempfile" + ".gettempdir",
        "jarvis.utils.paths.tempfile" + ".gettempdir",
    ]
    offenders = [
        f"{path.name}:{lineno}"
        for path in sorted(Path(__file__).parent.glob("*.py"))
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if any(pattern in line for pattern in forbidden)
    ]
    assert not offenders, (
        f"use JARVIS_STATE_DIR / JARVIS_BROWSER_PROFILE / JARVIS_LIVE_FLAG_DIR "
        f"instead of patching tempfile: {offenders}"
    )


def test_the_relay_state_file_is_redirected() -> None:
    # Asserted through the environment rather than by importing the relay: the
    # relay binds its state path at import time, so importing it here is the
    # trap this override exists to avoid.
    raw = os.environ.get("RELAY_STATE_PATH", "")
    assert raw, "RELAY_STATE_PATH must be set for the run"
    assert Path(raw).is_absolute()
    assert str(_isolated_root()) in raw
