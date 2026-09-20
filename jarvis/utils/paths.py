"""Where Jarvis keeps the state it writes.

Every default state location is resolved here rather than recomputed from
``__file__`` at each call site, so there is one answer to "where does this go"
and one way to move it. Two overrides exist:

* ``JARVIS_STATE_DIR`` - relocates the repo-anchored files (the memory store,
  ``memory.txt``, the chat log, screenshots and trajectories). Unset, the paths
  are exactly what they always were: the project root.
* ``JARVIS_BROWSER_PROFILE`` - relocates the persistent browser profile that
  otherwise lives in the user's home directory.

They exist so the application can be run against a scratch state directory, and
so the test suite never reads or writes the real user's state.
"""

from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    """The repository/project root, independent of the working directory."""
    return Path(__file__).resolve().parent.parent.parent


def state_root() -> Path:
    """Root for repo-anchored state; ``JARVIS_STATE_DIR`` when set."""
    override = os.environ.get("JARVIS_STATE_DIR", "").strip()
    return Path(override).expanduser() if override else project_root()


def browser_profile_dir() -> Path:
    """The persistent browser profile; ``JARVIS_BROWSER_PROFILE`` when set."""
    override = os.environ.get("JARVIS_BROWSER_PROFILE", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".jarvis" / "browser_profile"
