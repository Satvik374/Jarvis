"""A session's request to end itself.

Jarvis can already be stopped from outside - the user clicks END SESSION, or the
runtime receives ``:quit``. Both are *someone else's* decision arriving over a
control channel the agent never touches. This module is the other direction: the
agent deciding, mid-task, that its own session should end, and asking for it.

Why a module-level request instead of a return value threaded through the call
stack: the ask is made by a tool handler deep inside the agent loop, but it has
to be *honoured* by whoever owns the session - the console REPL, the browser
worker, the remote worker. Those run in different frames, and in browser mode a
different process. A single first-wins request is the one thing all of them can
observe without any of them knowing about each other.

The first request wins. A session is one session: a second ``stop_session`` call
(by the model retrying, or by a queued task) must not replace the reason the
user is about to read, and must not re-fire the listeners that have already
begun the shutdown.

Listeners are the prompt path. The console REPL notices the request when it next
regains control, which is immediate in the terminal but can be a long wait in
browser mode where the REPL is parked on a blocking read of its input pipe. A
listener lets the browser worker emit an event the parent can act on straight
away, instead of the page sitting on a session that has already decided to die.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

#: ``reason`` is shown to the user, so an empty one is filled by the handler
#: rather than being allowed to reach the transcript as a blank line.
_MAX_REASON = 400

_LOCK = threading.RLock()
_request: dict[str, Any] | None = None
_listeners: list[Callable[[dict[str, Any]], None]] = []


def clean_reason(reason: Any) -> str:
    """Normalise a reason to the exact form this module stores.

    Public because callers that need to ask "is this the reason of record?"
    have to compare against the stored value, and the stored value is the
    normalised one: a raw string with a double space in it would never match,
    and the mismatch would read as "someone else already asked" on the very
    first request. Idempotent, so applying it twice is harmless.
    """
    text = " ".join(str(reason or "").split())
    if len(text) > _MAX_REASON:
        text = text[: _MAX_REASON - 1].rstrip() + "…"
    return text


def request_session_stop(reason: str = "", source: str = "agent") -> dict[str, Any]:
    """Ask for the current session to end. Returns the request of record.

    Idempotent: the first call establishes the record, and every later call
    returns it unchanged. Callers can therefore treat the return value as "the
    reason this session is stopping" without having to track who spoke first.
    """
    global _request

    record = {
        "reason": clean_reason(reason),
        "source": str(source or "agent"),
        "at": time.time(),
    }

    with _LOCK:
        if _request is not None:
            return dict(_request)
        _request = record
        listeners = list(_listeners)

    # Fire outside the lock: a listener that emits an event, or that itself
    # touches this module, must never be able to deadlock the request.
    for listener in listeners:
        try:
            listener(dict(record))
        except Exception:  # pragma: no cover - a listener must not block the stop
            pass

    return dict(record)


def session_stop_requested() -> bool:
    """True once anything has asked the current session to end."""
    with _LOCK:
        return _request is not None


def session_stop_reason() -> str | None:
    """The reason of record, or ``None`` while the session is still running."""
    with _LOCK:
        return None if _request is None else str(_request.get("reason", ""))


def session_stop_source() -> str | None:
    """Who asked: ``agent``, ``user``, ``parent``… or ``None``."""
    with _LOCK:
        return None if _request is None else str(_request.get("source", ""))


def session_stop_record() -> dict[str, Any] | None:
    """A copy of the full request, or ``None``. Safe to hand to callers."""
    with _LOCK:
        return None if _request is None else dict(_request)


def clear_session_stop() -> None:
    """Forget the request.

    Only for a fresh session in the same process (a new REPL after a stop, a
    test, ``:reset``-style flows). Clearing it while the old session is still
    shutting down would let a second stop be queued behind the first.
    """
    global _request
    with _LOCK:
        _request = None


def add_session_stop_listener(
    listener: Callable[[dict[str, Any]], None],
) -> Callable[[], None]:
    """Register ``listener`` for the next request. Returns an unsubscribe.

    If a request already exists the listener fires immediately (once), so a
    runtime that registers late still learns the session is ending rather than
    waiting for a second request that will never come.
    """
    with _LOCK:
        if _request is None:
            _listeners.append(listener)
            return lambda: _remove_listener(listener)
        existing = dict(_request)

    try:
        listener(existing)
    except Exception:  # pragma: no cover - see request_session_stop
        pass
    return lambda: None


def _remove_listener(listener: Callable[[dict[str, Any]], None]) -> None:
    with _LOCK:
        try:
            _listeners.remove(listener)
        except ValueError:
            pass
