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
from pathlib import Path

from jarvis.agent import memory as agent_memory
from jarvis.browser_engine import driver
from jarvis.memory import manager as memory_manager
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


def test_the_browser_profile_is_not_the_users_real_profile() -> None:
    real_profile = Path.home() / ".jarvis" / "browser_profile"
    assert paths.browser_profile_dir() == _isolated_root() / "browser_profile"
    assert paths.browser_profile_dir() != real_profile


def test_the_browser_worker_anchors_screenshots_to_the_state_root() -> None:
    source = inspect.getsource(driver._BrowserWorker.__init__)
    assert "state_root()" in source
    # The original expression anchored screenshots to the repository itself.
    assert 'Path(__file__).resolve().parent.parent.parent / "dataset"' not in source


def test_the_live_voice_flag_is_not_the_machine_global_one() -> None:
    # The shared flag is what made the latency tests fail whenever a real live
    # session was running on the machine.
    assert _isolated_root() in voice._live_flag_path().parents


def test_the_relay_state_file_is_redirected() -> None:
    raw = os.environ.get("RELAY_STATE_PATH", "")
    assert raw, "RELAY_STATE_PATH must be set for the run"
    assert Path(raw).is_absolute()
    assert str(_isolated_root()) in raw
