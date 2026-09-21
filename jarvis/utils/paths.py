"""Where Jarvis keeps the state it writes.

Every default state location is resolved here rather than recomputed from
``__file__`` at each call site, so there is one answer to "where does this go"
and one way to move it.

Three levels, in order - the order the functions below actually apply:

1. the environment overrides ``JARVIS_STATE_DIR``, ``JARVIS_BROWSER_PROFILE`` and
   ``JARVIS_LIVE_FLAG_DIR`` - how a user relocates the application's state, and
   how a test points a single location at its own directory instead of the
   run's sandbox;
2. an explicit in-process sandbox (:func:`set_sandbox_root`) - used by the test
   suite, because it survives a test that does
   ``patch.dict(os.environ, {}, clear=True)``: that drops the overrides above,
   and must not drop the sandbox with them;
3. the historical defaults: the project root, ``~/.jarvis/browser_profile``, and
   the temp directory for the live-voice flag.

Every location is resolved through :func:`_base_for`, so the order above is
stated once and applies to all of them. With nothing set the paths are exactly
what they always were.

Deliberately *not* here: paths that must keep pointing at the installed program
rather than at its state - ``config.yaml``/``.env`` inputs, the startup shortcut
that relaunches ``run.py``, and the browser worker's ``sys.path`` bootstrap. A
state override must not be able to move those.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Callable

#: The live-voice flag is named here, next to the rule for where it lives.
LIVE_FLAG_NAME = "jarvis_live_mode.flag"

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


def _base_for(env_key: str, sandbox_subdir: str | None,
              default: Callable[[], Path]) -> Path:
    """The one resolution order: explicit override, sandbox, historical default.

    ``sandbox_subdir`` names the directory the sandbox uses for this location so
    one sandbox root can hold several stores side by side. ``default`` is a
    callable because computing it can need the environment - ``Path.home()`` and
    ``tempfile.gettempdir()`` both consult it - and a test that clears
    ``os.environ`` must still resolve to its sandbox.
    """
    override = os.environ.get(env_key, "").strip()
    if override:
        return Path(override).expanduser()
    if _sandbox_root is not None:
        return _sandbox_root / sandbox_subdir if sandbox_subdir else _sandbox_root
    return Path(default())


def state_root() -> Path:
    """Root for repo-anchored state."""
    return _base_for("JARVIS_STATE_DIR", None, project_root)


def browser_profile_dir() -> Path:
    """The persistent browser profile."""
    return _base_for(
        "JARVIS_BROWSER_PROFILE", "browser_profile",
        lambda: Path.home() / ".jarvis" / "browser_profile",
    )


def live_flag_path() -> Path:
    """The flag a live voice session heartbeats for the machine.

    Temp-anchored rather than repo-anchored: it is shared between processes that
    need not run from the same directory, so its default is the temp directory
    rather than the project root. It lives here, with the other locations, so
    "where does state go" has one answer instead of one per module.
    """
    base = _base_for("JARVIS_LIVE_FLAG_DIR", "live-flag", tempfile.gettempdir)
    return base / LIVE_FLAG_NAME
