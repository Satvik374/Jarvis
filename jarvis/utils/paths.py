"""Where Jarvis keeps the state it writes.

Every default state location is resolved here rather than recomputed from
``__file__`` at each call site, so there is one answer to "where does this go"
and one way to move it.

Three levels, in order - the order the functions below actually apply:

1. the environment overrides ``JARVIS_STATE_DIR`` and ``JARVIS_BROWSER_PROFILE``
   - how a user relocates the application's state, and how a test points a
   single location at its own directory instead of the run's sandbox;
2. an explicit in-process sandbox (:func:`set_sandbox_root`) - used by the test
   suite, because it survives a test that does
   ``patch.dict(os.environ, {}, clear=True)``: that drops the overrides above,
   and must not drop the sandbox with them;
3. the historical defaults: the project root, and ``~/.jarvis/browser_profile``.

With nothing set the paths are exactly what they always were.

Deliberately *not* here: paths that must keep pointing at the installed program
rather than at its state - ``config.yaml``/``.env`` inputs, the startup shortcut
that relaunches ``run.py``, and the browser worker's ``sys.path`` bootstrap. A
state override must not be able to move those.
"""

from __future__ import annotations

import os
from pathlib import Path

_sandbox_root: Path | None = None


def project_root() -> Path:
    """The repository/project root, independent of the working directory."""
    return Path(__file__).resolve().parent.parent.parent


def set_sandbox_root(path: Path | str | None) -> None:
    """Force every state location under ``path`` for this process.

    The environment override is not enough on its own: the test suite has sites
    that do ``patch.dict(os.environ, {}, clear=True)``, and clearing the
    environment made the state paths fall back to the repository, so a
    default-constructed store wrote real state. This override lives in the
    process, so clearing the environment cannot escape it. Pass ``None`` to
    release it, which restores the environment/default behaviour.
    """
    global _sandbox_root
    _sandbox_root = Path(path).expanduser() if path is not None else None


def sandbox_root() -> Path | None:
    """The forced sandbox root, or ``None`` when no sandbox is active."""
    return _sandbox_root


def state_root() -> Path:
    """Root for repo-anchored state."""
    override = os.environ.get("JARVIS_STATE_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    if _sandbox_root is not None:
        return _sandbox_root
    return project_root()


def browser_profile_dir() -> Path:
    """The persistent browser profile."""
    override = os.environ.get("JARVIS_BROWSER_PROFILE", "").strip()
    if override:
        return Path(override).expanduser()
    if _sandbox_root is not None:
        return _sandbox_root / "browser_profile"
    return Path.home() / ".jarvis" / "browser_profile"
