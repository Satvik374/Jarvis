"""Shared test isolation.

Live voice *measures* which models hear audio and writes the verdict to
``~/.jarvis/live_audio_models.json`` (see ``jarvis.live.gemini_live``), because
that verdict is a property of the API key rather than of a process. Tests must
not read it - a model demoted on the machine running the suite would silently
reorder another test's expectations - and must not write it either.

The same reasoning applies to every other store Jarvis keeps outside a test's
own temporary directory, which is what ``_isolated_state`` below redirects: the
memory database, ``memory.txt``, the chat log, screenshots, trajectories, the
persistent browser profile, the relay state file and the machine-global live
voice flag. A suite that touches those changes the developer's real data and,
worse, makes the result depend on what that data already contained.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True, scope="session")
def _isolated_live_audio_state(tmp_path_factory):
    """Point the measured live-voice verdicts at a throwaway file for the run."""
    state = tmp_path_factory.mktemp("live-state") / "live_audio_models.json"
    previous = os.environ.get("JARVIS_LIVE_AUDIO_STATE")
    os.environ["JARVIS_LIVE_AUDIO_STATE"] = str(state)
    yield state
    if previous is None:
        os.environ.pop("JARVIS_LIVE_AUDIO_STATE", None)
    else:
        os.environ["JARVIS_LIVE_AUDIO_STATE"] = previous


@pytest.fixture(autouse=True, scope="session")
def _isolated_state(tmp_path_factory):
    """Redirect every machine-global state location into this run's temp tree.

    Set before any test body runs and restored afterwards, so the real user's
    memory, chat log, screenshots, browser profile and live flag are never read
    or written by the suite. Inner per-test patches still take precedence.
    """
    # Resolve pytest's temp root first so the patched tempfile.gettempdir below
    # cannot influence where pytest itself creates directories.
    base = tmp_path_factory.getbasetemp() / "jarvis-isolation"
    base.mkdir(parents=True, exist_ok=True)
    flag_dir = base / "live-flag"
    flag_dir.mkdir(parents=True, exist_ok=True)

    overrides = {
        "JARVIS_STATE_DIR": str(base),
        "JARVIS_BROWSER_PROFILE": str(base / "browser_profile"),
        "RELAY_STATE_PATH": str(base / "relay_state.json"),
    }
    previous = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    _reset_memory_singleton()

    with patch("jarvis.utils.voice.tempfile.gettempdir", return_value=str(flag_dir)):
        try:
            yield base
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            _reset_memory_singleton()


def _reset_memory_singleton() -> None:
    """Drop the cached memory manager so it is rebuilt against the new paths."""
    from jarvis.memory import manager as memory_manager

    memory_manager._GLOBAL_MANAGER = None
