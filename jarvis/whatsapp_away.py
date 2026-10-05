"""The away assistant: answer WhatsApp callers while the user is away.

What the platform really allows - taken from Meta's Calling API docs rather than
assumed:

* Meta *does* deliver an incoming call to a webhook. The ``calls`` field carries
  the caller, an SDP offer, and a 30-60 second window to answer. Taking that
  call means replying with an SDP answer and carrying the audio over WebRTC.
  Jarvis has no media server, so a live *voice* conversation is not something
  this module can do, and it does not pretend to: it never accepts a call, never
  writes an SDP answer, and nothing here reports a call as "answered".
* What does work, with nothing extra, is the messaging half of the same webhook.
  A ringing call is met with a message, in words the real brain writes, saying the
  user is busy and asking what to pass on; the caller's answer is what gets
  relayed. A caller who writes first has already said what they wanted, so their
  own words are captured as they arrive instead of being handed back to them as a
  question to answer again. Either way the user's next return names who called and
  what each caller wanted - including a caller who rang and left no words at all,
  which would otherwise leave no trace.

So of the four behaviours: "answers the call" is answered *by text, promptly*,
and that is stated rather than faked; the other three are implemented here. The
caller-facing words are always a real completion - when the provider cannot be
reached nothing is sent, the caller is left for the next pass, and the report
says so, instead of a canned line standing in for a conversation.

Timing, because "automation" means a caller is served as they arrive rather than
noticed later: :func:`poll_and_answer` is the one entry point, and it runs from
both places. While the application is open the console starts a :class:`LiveWatch`,
which polls the inbox every :data:`LIVE_INTERVAL` seconds and announces each
caller the moment they answer; when the application is closed, the caller is
answered at the next start and reported in that startup briefing. Both passes
share one store and one set of event ids, so a caller is never answered twice.

One platform caveat, surfaced rather than hidden: Meta only allows a free-form
business message within 24 hours of the caller's own last *message*, and a call
event is not a message - so a number with no open window gets the reply refused,
and that refusal is reported rather than swallowed.

Where the pieces live: the WhatsApp payload shape and the actual send belong to
:mod:`jarvis.tools.connectors`; this module owns the conversation, what was
captured, and the report.

Its own state file is written atomically and is repairable. A write that cannot
land is kept in a spill beside it rather than dropping a caller who has already
been messaged, so nobody is told twice because a file was busy; and a state file
that cannot be read is set aside, named, and mentioned in the report, so the user
is never quietly told less than happened.
"""

from __future__ import annotations

import datetime
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .tools import connectors
from .utils import logging as log
from .utils.paths import state_root

#: State, beside every other store the application writes.
STATE_NAME = "whatsapp_away.json"
#: Where a reply goes when no WhatsApp token exists: the local driver reads it.
OUTBOX_NAME = "whatsapp_away_outbox.jsonl"

#: One pass costs one real completion per caller, so it is capped rather than
#: allowed to hold up startup for an unbounded number of callers.
MAX_PER_PASS = 5
# A save replaces the state file, and on Windows that replace is refused for an
# instant while a reader has it open - the watch, the report and the startup pass
# all read it. Both sides of that window are retried for a few tens of ms.
_RETRY_PAUSE = 0.01
_READ_TRIES = 4
_WRITE_TRIES = 10
#: How often the *running* application checks for a caller. Two seconds plus a
#: second or two of completion puts the busy reply a few seconds after the ring.
LIVE_INTERVAL = 2.0
#: How long a caller we could not reach is left alone before being tried again.
#: The live watch polls every couple of seconds, so without this an unreachable
#: provider would be hit on every poll - thirty requests a minute, for one caller.
FAILURE_RETRY = 30.0
_MAX_QUOTE = 140
_MAX_REPLY = 600
_MAX_HANDLED = 500
_MAX_RELAYS = 200
#: How many caller facts the current session remembers, for the ``:status``
#: dashboard. The greeting consumes the report, so without this memory the
#: dashboard would be empty the moment the user looked at it.
_MAX_SESSION = 30
#: Where the state goes when the real file cannot be replaced at all - something
#: outside Jarvis is holding it open. The spill is the same state, in a file no
#: reader is holding, so a caller who has already been messaged stays on record and
#: is never told twice; the next save that works folds it back and deletes it.
_SPILL_SUFFIX = ".unsaved"
#: A state file that could not be read is moved here rather than overwritten, so
#: the content is kept for a look instead of vanishing.
_CORRUPT_MARK = ".corrupt-"
#: The two ways the state can arrive damaged, as the report says them.
_PARTLY_UNREADABLE = {
    "key": "damage:partly", "at": "",
    "detail": "Part of the WhatsApp callers log was unreadable, so some callers "
              "may be missing",
    "reported": False}
STATE_FIELDS = ("handled", "relays", "rings", "failed", "damage")

#: Conversation stage per caller: we have told them the owner is away and asked
#: what to pass on. Everything they say afterwards is what we capture.
ASKED = "asked"
RELAYED = "relayed"

#: The instructions, not the words. The words come from the model; these only say
#: what the reply has to achieve. Nothing here is ever sent to a caller.
_ASK_SYSTEM = (
    "You are Jarvis, answering this WhatsApp for the person the caller is trying "
    "to reach, who is away from their phone right now.\n\n"
    "Write the reply to send to the caller, in the first person, the way a "
    "helpful assistant answers the phone. In one or two short sentences: say "
    "that they are busy at the moment, and ask what the caller would like you to "
    "pass on to them.\n\n"
    "Reply with the message text only - no quotes, no JSON, no preamble, no "
    "signature, no emoji. Write in the language they used, or in English when "
    "they sent no text. Promise nothing, do not say your owner will call back, "
    "and invent no facts you were not given.")

_ACK_SYSTEM = (
    "You are Jarvis, answering this WhatsApp for the person the caller is trying "
    "to reach, who is away from their phone right now.\n\n"
    "The caller has said what they want passed on - either because you asked them, "
    "or in the very first message they sent. Write the one short sentence that "
    "acknowledges it: that the person they are trying to reach is busy at the "
    "moment, that you will pass it on, and thanks. Nothing else.\n\n"
    "Reply with the message text only - no quotes, no JSON, no preamble, no "
    "emoji, and do not repeat their message back to them.")


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #

def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _iso(ts) -> str:
    """A stored time: the event's own timestamp when it had one, else now."""
    if isinstance(ts, int) and ts:
        return datetime.datetime.fromtimestamp(ts).isoformat(timespec="seconds")
    return _now_iso()


def _clock(when) -> str:
    """``"14:40"`` from a stored time or a Unix timestamp; ``""`` when unknown."""
    if isinstance(when, str) and when:
        try:
            return datetime.datetime.fromisoformat(when).strftime("%H:%M")
        except ValueError:
            return ""
    if isinstance(when, int) and when:
        return datetime.datetime.fromtimestamp(when).strftime("%H:%M")
    return ""


def _key(ev: dict) -> str:
    """A stable identity per event, so polling the same file twice is safe."""
    if ev.get("message_id"):
        return f"m:{ev['message_id']}"
    if ev.get("call_id"):
        return f"c:{ev['call_id']}:{ev.get('event', '')}"
    return f"x:{ev.get('sender')}:{ev.get('ts')}:{str(ev.get('text', ''))[:40]}"


def _clean(text: str) -> str:
    """One line of plain prose from a completion, or ``""`` when unusable."""
    body = " ".join(str(text or "").split()).strip().strip('"').strip()
    if body.startswith("{"):
        # The configured model also speaks the agent loop's JSON action envelope.
        # A message hidden inside one is usable; anything else is not.
        try:
            data = json.loads(body)
        except Exception:
            return ""
        body = ""
        for name in ("message", "reply", "text", "body", "answer"):
            value = data.get(name) if isinstance(data, dict) else None
            if isinstance(value, str) and value.strip():
                body = " ".join(value.split())
                break
        if not body:
            return ""
    return body[:_MAX_REPLY]


def _reason(exc: BaseException) -> str:
    """A short, plain reason for the report - the message, not a traceback."""
    text = " ".join(str(exc).split())
    return (text[:160] or exc.__class__.__name__)


def _who(ev: dict) -> str:
    """The caller's name when WhatsApp gave one, else their number."""
    return str(ev.get("name") or ev.get("sender") or "someone")


def _ring_line(ring: dict) -> str:
    """``Ravi called at 14:40`` - a ring that never became words.

    The count in front of the list already says these ones left nothing, so the
    line itself does not repeat it.
    """
    verb = "called" if ring.get("origin") == "call" else "messaged"
    when = _clock(ring.get("at"))
    return (f"{ring.get('who', 'someone')} {verb}"
            f"{f' at {when}' if when else ''}")


def _caller_line(relays: list[dict]) -> str:
    """One caller's line, keeping every message they sent.

    Somebody who writes twice said two things; quoting only the first would drop
    half of what they wanted passed on, so each message is quoted.
    """
    first = relays[0]
    said = [" ".join(str(r.get("text", "")).split())[:_MAX_QUOTE] for r in relays]
    said = [s for s in said if s]
    if not said:
        return _ring_line(first)
    if len(said) == 1:
        return _relay_line(first)
    verb = "called" if first.get("origin") == "call" else "messaged"
    when = _clock(first.get("at"))
    return (f"{first.get('who', 'someone')} {verb}"
            f"{f' at {when}' if when else ''} and said "
            + " and also ".join(f'"{s}"' for s in said))


def _per_caller(relays: list[dict]) -> list[str]:
    """One line per caller, in arrival order, however many times they wrote."""
    grouped: dict[str, list[dict]] = {}
    for relay in relays:
        grouped.setdefault(str(relay.get("wa_id") or relay.get("who") or "?"),
                           []).append(relay)
    return [_caller_line(items) for items in grouped.values()]


def _silent(rings: list[dict], relays: list[dict]) -> list[dict]:
    """Rings that never turned into words - the words replace them, per caller.

    The one owner of that rule: somebody who rang and then left word is quoted,
    not described twice. Returns the records rather than rendered lines, so the
    caller can still ask where each one came from (a call gets the plain note
    about voice).
    """
    spoke = {str(r.get("wa_id") or r.get("who")) for r in relays}
    out: list[dict] = []
    seen: set[str] = set()
    for ring in rings:
        who = str(ring.get("wa_id") or ring.get("who"))
        if who in spoke or who in seen:
            continue
        seen.add(who)
        out.append(ring)
    return out


def _tally(callers: int, silent: int) -> list[str]:
    """The report's opening count: callers who left word, and rings that did not."""
    parts = []
    if callers:
        parts.append(f"{callers} WhatsApp "
                     f"{'caller' if callers == 1 else 'callers'} left word")
    if silent:
        parts.append(f"{silent} caller{'s' if silent != 1 else ''} rang and "
                     f"left no message")
    return parts


def _unreadable(where: str) -> dict:
    """The report entry for a state file that could not be read at all."""
    return {"key": "damage:unreadable", "at": _now_iso(),
            "detail": (f"The WhatsApp callers log could not be read and has been "
                       f"set aside as {Path(where).name} - anything recorded in "
                       f"it is missing"),
            "reported": False}


def _damage_line(entry: dict) -> str:
    """One plain sentence about something that could not be read, without its stop."""
    detail = str(entry.get("detail") or
                 "The WhatsApp callers log could not be read, so some callers may "
                 "be missing")
    return detail.rstrip(".")            # the report ends the sentence itself


def _clean_state(raw: dict) -> tuple[dict, bool]:
    """The state as the rest of the module reads it, and whether it had to be fixed.

    A field of the wrong shape is dropped rather than trusted: a hand-edited or
    half-written file must not make the report raise, or a pass die, or a caller
    be answered twice. Dropping anything is reported, never silent.
    """
    clean: dict = {}
    fixed = False
    for name, value in raw.items():
        if name in STATE_FIELDS:
            if not isinstance(value, list):
                fixed = True
                continue
            kept = ([k for k in value if isinstance(k, str)] if name == "handled"
                    else [e for e in value if isinstance(e, dict)])
            fixed = fixed or len(kept) != len(value)
            clean[name] = kept
        elif name == "callers":
            if not isinstance(value, dict):
                fixed = True
                continue
            kept_callers = {str(k): v for k, v in value.items()
                            if isinstance(v, dict)}
            fixed = fixed or len(kept_callers) != len(value)
            clean[name] = kept_callers
        else:
            clean[name] = value        # unknown keys are left exactly as they are
    return clean, fixed


def _relay_line(relay: dict) -> str:
    """One caller in their own words: ``Ravi called at 14:40 and said "…"``.

    Used by the report and by the live announcement, so what you read when a
    caller arrives mid-session is the same sentence you would read at startup.
    """
    said = " ".join(str(relay.get("text", "")).split())[:_MAX_QUOTE]
    verb = "called" if relay.get("origin") == "call" else "messaged"
    when = _clock(relay.get("at"))
    return (f"{relay.get('who', 'someone')} {verb}"
            f"{f' at {when}' if when else ''} and said \"{said}\"")


# --------------------------------------------------------------------------- #
# results
# --------------------------------------------------------------------------- #

@dataclass
class Reply:
    """One outgoing message and how it actually left."""
    to: str
    body: str
    via: str                  # "whatsapp cloud API" or "local bridge"
    delivered: bool           # False when the platform refused it
    error: str = ""


@dataclass
class Outcome:
    """What one inbound event turned into."""
    reply: Optional[Reply] = None
    relay: Optional[dict] = None      # what the caller wanted passed on, stored
    failure: Optional[dict] = None    # a caller still owed a reply; retry later
    asked: Optional[dict] = None      # a caller just told the user is busy


# --------------------------------------------------------------------------- #
# the automation
# --------------------------------------------------------------------------- #

class Away:
    """The one owner of the away conversation: events in, replies and a report out."""

    def __init__(self, state: Path | str | None = None,
                 inbox: Path | str | None = None,
                 outbox: Path | str | None = None) -> None:
        self.state_path = Path(state) if state else state_root() / STATE_NAME
        self.outbox_path = Path(outbox) if outbox else state_root() / OUTBOX_NAME
        self._inbox = Path(inbox) if inbox else None
        self.spill_path = self.state_path.with_name(self.state_path.name
                                                    + _SPILL_SUFFIX)
        self._lock = threading.Lock()
        #: Set when this process found the state damaged; said once, in the report.
        self._damage: Optional[dict] = None
        #: What this session has already told the user, per caller, for ``:status``.
        #: Keyed by caller so a second message updates their line instead of
        #: adding a second one: the dashboard then reads the way the report does.
        self._told: dict[str, str] = {}

    # ---- where things live ------------------------------------------------ #

    def inbox_path(self) -> Optional[Path]:
        """The file events arrive in - the webhook's file, or the driver's."""
        return self._inbox or connectors.whatsapp_inbox_path()

    # ---- state ------------------------------------------------------------ #

    def load(self) -> dict:
        """The state, from the newest copy of it, with its shape checked.

        Three things make this more than a file read. The save below replaces this
        file, and on Windows that replace is refused for an instant while a reader
        has it open, so the read itself is retried - and a save that never got
        through leaves a *spill*, which is newer, and is read in preference to the
        file it could not replace. A file whose shape is wrong is repaired field by
        field rather than trusted. A file that cannot be read at all is set aside
        and named. The last two are then *said* in the report, because quietly
        telling the user less than happened is the one outcome that is not allowed.
        """
        spill, main = self._read(self.spill_path), self._read(self.state_path)
        state = self._newest(spill, main)
        if state is None:
            if self.spill_path.exists() or self.state_path.exists():
                self._damage = self._damage or _unreadable(self._quarantine())
            return self._with_damage({})
        clean, repaired = _clean_state(state)
        if repaired:
            self._damage = self._damage or dict(_PARTLY_UNREADABLE, at=_now_iso())
        return self._with_damage(clean)

    def save(self, state: dict) -> None:
        """Write atomically, and never lose what has already been done.

        The replace is retried because Windows refuses it while a reader holds the
        file. If it still cannot land, the same state is spilled to a second file
        that no reader is holding: the caller who has already been messaged stays
        on record and is never told twice, the pass carries on to the next caller
        instead of dying, and the next save that works folds the spill back in and
        removes it. A write that never gets through leaves no temp file behind.
        """
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_name(self.state_path.name + ".tmp")
        tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
        for attempt in range(_WRITE_TRIES):
            try:
                os.replace(tmp, self.state_path)
            except OSError:
                if attempt == _WRITE_TRIES - 1:
                    tmp.unlink(missing_ok=True)
                    self._spill(state)
                    return
                time.sleep(_RETRY_PAUSE)
            else:
                self._clear_spill()
                return

    def _read(self, path: Path) -> Optional[dict]:
        """One state file as a dict; ``None`` when it is missing or unreadable."""
        for attempt in range(_READ_TRIES):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return None
            except Exception:
                if attempt == _READ_TRIES - 1:
                    return None
                time.sleep(_RETRY_PAUSE)
            else:
                return data if isinstance(data, dict) else None
        return None

    def _newest(self, spill: Optional[dict], main: Optional[dict]) -> Optional[dict]:
        """The spill wins unless the real file has been written since it."""
        if spill is None:
            return main
        if main is None:
            return spill
        try:
            if self.spill_path.stat().st_mtime >= self.state_path.stat().st_mtime:
                return spill
        except OSError:
            return spill
        return main

    def _with_damage(self, state: dict) -> dict:
        """Attach the note about a log that could not be read, exactly once.

        Matched on the note's key, not its content: once it has been said and
        marked, the copy on file carries that mark, so comparing whole entries
        would put the note back every time and nag forever.
        """
        if self._damage is None:
            return state
        damage = state.get("damage")
        if not isinstance(damage, list):
            damage = []
        if not any(d.get("key") == self._damage.get("key") for d in damage):
            damage.append(self._damage)
        state["damage"] = damage[-5:]
        return state

    def _quarantine(self) -> str:
        """Move a state file that could not be read aside, and say where it went.

        Best effort: the rename is refused too while a reader holds the file, in
        which case its own path is named and it is left where it is. Either way the
        user is told, rather than the content quietly disappearing.
        """
        aside = self.state_path.with_name(
            f"{self.state_path.name}{_CORRUPT_MARK}"
            f"{datetime.datetime.now():%Y%m%d-%H%M%S}")
        try:
            os.replace(self.state_path, aside)
            return str(aside)
        except OSError:
            return str(self.state_path)

    def _spill(self, state: dict) -> None:
        """Keep the state where no reader can block it, and say that it happened.

        Said once per spell of trouble, not once per poll: the watch retries every
        couple of seconds and repeating the same warning would bury the console.
        """
        told = self.spill_path.exists()
        try:
            self.spill_path.write_text(json.dumps(state, indent=1),
                                       encoding="utf-8")
        except OSError as exc:
            log.error(f"away: nowhere to keep the callers log: {exc}")
            return
        if not told:
            log.warn(f"away: the callers log is held open by something else, so "
                     f"this pass was kept in {self.spill_path.name} instead")

    def _clear_spill(self) -> None:
        """The real file is up to date again, so the spill has nothing left to say."""
        try:
            self.spill_path.unlink(missing_ok=True)
        except OSError:
            pass

    def recover(self) -> bool:
        """Fold a spill back into the real file once it can be written again.

        A save that could not land leaves the spill, and nothing else may happen
        for a while afterwards - so the next pass asks for this: the state goes
        back where it belongs and the spill goes away, rather than waiting for
        another caller to change something before the file catches up.
        """
        if not self.spill_path.exists():
            return False
        with self._lock:
            self.save(self.load())
        return not self.spill_path.exists()

    # ---- inbound ---------------------------------------------------------- #

    def events(self) -> list[dict]:
        """Every inbound event in the inbox, oldest first.

        Reads the whole file each time - these inboxes are chat logs, and the
        events already answered are skipped by :meth:`handle` via their ids.
        """
        path = self.inbox_path()
        if path is None or not path.exists():
            return []
        out: list[dict] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            out.extend(connectors.whatsapp_events(payload))
        return out

    # ---- the conversation ------------------------------------------------- #

    def handle(self, ev: dict, brain) -> Outcome:
        """One event, start to finish: reply in the brain's words, capture theirs."""
        key = _key(ev)
        with self._lock:
            state = self.load()
            handled = state.setdefault("handled", [])
            if key in handled:
                return Outcome()
            if self._too_soon(state, key):
                # Tried moments ago and it failed; waiting is the whole point of
                # the window. Left unhandled, so it is retried once it passes.
                return Outcome()
            caller = str(ev.get("sender") or "?")
            record = state.setdefault("callers", {}).setdefault(caller, {})
            if ev.get("name"):
                record["name"] = str(ev["name"])

            # A call always opens (or re-opens) the conversation: it cannot be
            # answered by voice, so the caller is messaged instead. A terminate
            # event is the tail of a call already dealt with, so it needs nothing.
            if ev.get("kind") == "call":
                if ev.get("event") and ev["event"] != "connect":
                    self._finish(state, handled, key)
                    self.save(state)
                    return Outcome()
                replying, relay = "ask", ""
            else:
                # A message *is* what they want passed on, so their own words are
                # captured straight away instead of being met with a request to
                # repeat them - a caller who writes the whole thing in one go used
                # to lose it. A message with no words at all (a photo, a voice
                # note) is asked the same way a ringing call is.
                replying, relay = "ack", str(ev.get("text", ""))
                if not relay:
                    replying = "ask"

            if relay:
                record["stage"] = RELAYED
            elif replying == "ask":
                record["stage"] = ASKED
                record.setdefault("origin",
                                  "call" if ev.get("kind") == "call" else "message")

            system = _ASK_SYSTEM if replying == "ask" else _ACK_SYSTEM
            try:
                body = self._compose(brain, system, _prompt(ev, relay))
            except Exception as exc:
                # Nothing goes out and the event stays unhandled, so the next
                # pass tries again. A canned line is never substituted.
                entry = {"who": _who(ev), "wa_id": caller, "key": key,
                         "reason": _reason(exc), "at": _now_iso(),
                         "reported": False}
                state.setdefault("failed", []).append(entry)
                state["failed"] = state["failed"][-_MAX_RELAYS:]
                self.save(state)
                return Outcome(failure=entry)

            reply = self._deliver(caller, body)
            stored: Optional[dict] = None
            failure: Optional[dict] = None
            asked: Optional[dict] = None
            if replying == "ask" and reply.delivered:
                # Worth telling the user about on its own: a caller who rings and
                # hangs up without leaving word would otherwise leave no trace, so
                # the ring is kept for the report as well as announced live. If
                # they go on to leave word, the report quotes the words instead -
                # that is :func:`_silent`'s job, so it lives in one place.
                asked = {"key": key, "who": _who(ev), "wa_id": caller,
                         "origin": ("call" if ev.get("kind") == "call"
                                    else "message"),
                         "at": _iso(ev.get("ts")), "reported": False,
                         "via": reply.via}
                state.setdefault("rings", []).append(asked)
                state["rings"] = state["rings"][-_MAX_RELAYS:]
            if relay:
                stored = {"key": key, "who": _who(ev), "wa_id": caller,
                          "origin": record.get("origin", "message"),
                          "text": relay, "at": _iso(ev.get("ts")),
                          "reported": False}
                state.setdefault("relays", []).append(stored)
                state["relays"] = state["relays"][-_MAX_RELAYS:]
            if reply.delivered:
                self._finish(state, handled, key)
            else:
                # Meta refused the reply - a call with no open 24-hour window is
                # the common case. Recorded so the report says so, and left
                # unhandled so the next pass tries again rather than the caller
                # being dropped without a word.
                failure = {"who": _who(ev), "wa_id": caller, "key": key,
                           "reason": reply.error or "the reply could not be sent",
                           "at": _now_iso(), "reported": False}
                state.setdefault("failed", []).append(failure)
                state["failed"] = state["failed"][-_MAX_RELAYS:]
            self.save(state)
            return Outcome(reply=reply, relay=stored, failure=failure, asked=asked)

    @staticmethod
    def _compose(brain, system: str, user: str) -> str:
        """The caller-facing words, from the real brain - never a canned line.

        The brain's own retry policy, in its short profile: somebody is waiting on
        the other end, so a busy provider must fail in seconds rather than hold a
        caller for a minute and a half.
        """
        from .agent.brain import complete_with_retry

        body = _clean(complete_with_retry(
            brain, system, [{"role": "user", "content": user}],
            tries=2, task_patience=False))
        if not body:
            raise ValueError("the model answered with nothing usable")
        return body

    def _deliver(self, to: str, body: str) -> Reply:
        """Send through the Cloud API when tokens exist, else to the local outbox.

        The outbox is not a pretend send: it is the file the local driver reads,
        and ``via`` says which of the two happened, so no surface can claim a
        caller received something that never left the machine.
        """
        if connectors.whatsapp_can_send():
            try:
                connectors.whatsapp_send_text(to, body)
                return Reply(to, body, "whatsapp cloud API", True)
            except Exception as exc:
                return Reply(to, body, "whatsapp cloud API", False, _reason(exc))
        try:
            self.outbox_path.parent.mkdir(parents=True, exist_ok=True)
            with self.outbox_path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps({"to": to, "body": body, "at": _now_iso(),
                                     "via": "local bridge"}) + "\n")
        except Exception as exc:
            return Reply(to, body, "local bridge", False, _reason(exc))
        return Reply(to, body, "local bridge", True)

    @staticmethod
    def _finish(state: dict, handled: list, key: str) -> None:
        handled.append(key)
        del handled[:-_MAX_HANDLED]

    @staticmethod
    def _too_soon(state: dict, key: str) -> bool:
        """True when this event failed so recently that retrying is hammering.

        Reads :data:`FAILURE_RETRY` at call time rather than binding it as a
        default, so the window can be shortened in a test without touching the
        production value.
        """
        for item in state.get("failed", []):
            if item.get("key") != key:
                continue
            try:
                when = datetime.datetime.fromisoformat(str(item.get("at", "")))
            except ValueError:
                return False
            return (datetime.datetime.now() - when).total_seconds() < FAILURE_RETRY
        return False

    # ---- outbound: the report --------------------------------------------- #

    def report(self, mark: bool = False, limit: int = 5) -> str:
        """What each caller wanted, as one plain sentence.

        Names and words are the caller's own - no model paraphrases them - so
        "who said what" is exactly what they said. One line per *caller*, so
        somebody who writes three times is one caller with three things to pass
        on rather than "3 callers" - the count has to be true about people, not
        about messages. A caller who rang and left no words is named too, since a
        ring that never became a message would otherwise leave no trace at all.
        ``mark`` consumes the entries; the startup briefing passes it, because
        that is the moment you are back. Takes the same lock as :meth:`handle`, so
        a caller arriving while the report is being written is neither lost nor
        reported twice. Anything the state could not tell us - a file that could
        not be read, or was repaired - is said here too, because quietly saying
        less than happened is the one outcome this must never have.
        """
        with self._lock:
            state = self.load()
            relays = [r for r in state.get("relays", []) if not r.get("reported")]
            rings = [r for r in state.get("rings", []) if not r.get("reported")]
            failed = [f for f in state.get("failed", []) if not f.get("reported")]
            damage = [d for d in state.get("damage", []) if not d.get("reported")]
            if not relays and not rings and not failed and not damage:
                return ""

            callers = _per_caller(relays)
            silent = _silent(rings, relays)
            sentences: list[str] = []
            if callers or silent:
                lead = ("While you were away: "
                        + " and ".join(_tally(len(callers), len(silent))))
                if any(r.get("origin") == "call" for r in relays + silent):
                    lead += (" - a WhatsApp voice call cannot be answered here, so "
                             "calls were followed up by message")
                lines = callers + [_ring_line(r) for r in silent]
                sentences.append(lead + ": " + "; ".join(lines[:limit]))
            if failed:
                if not sentences:
                    sentences.append("While you were away")
                sentences.append(
                    f"{len(failed)} caller(s) could not be answered - "
                    f"{failed[0].get('reason', 'the AI service was unavailable')}")
            for entry in damage:
                # Unreadable state is a fact about the report, so it is said.
                sentences.append(_damage_line(entry))
            text = ". ".join(sentences) + "."
            if mark:
                for item in (state.get("relays", []) + state.get("rings", [])
                             + state.get("failed", []) + damage):
                    item["reported"] = True
                self.save(state)
        if mark:
            # Told once, and remembered for the rest of the session so the
            # ``:status`` dashboard shows the same facts instead of an empty line.
            self._remember(text)
        return text

    # ---- outbound: the live announcement ---------------------------------- #

    def announce(self, record: dict, unanswered: bool = False) -> None:
        """Say one caller now, on the surfaces a *running* app shows.

        Used by the live watch: the console notice and the browser alert (the
        existing log bridge forwards it) are the two places you actually read
        while the app is open, so a caller who arrives mid-session is reported the
        moment they answer instead of at the next start. This notice *is* the
        report, so the record is marked reported and the next briefing does not
        repeat it.
        """
        if unanswered:
            message = (f"could not answer {record.get('who', 'a caller')} - "
                       f"{record.get('reason', 'the AI service was unavailable')}")
        else:
            message = _relay_line(record)
        self._tell(message, record,
                   fact="" if unanswered else self._caller_fact(record))

    def announce_ask(self, record: dict) -> None:
        """Tell the user a caller just arrived, before they say what they want.

        The news that somebody rang; if they go on to leave word, that is announced
        separately. A ring with no message is now part of the report too, so this
        notice settles it - the next start stays quiet about something the user has
        already been told. What it says about the caller being told is what
        actually happened: with no WhatsApp token the reply only reaches the
        outbox, and the notice says so rather than claiming a delivery.
        """
        verb = "called" if record.get("origin") == "call" else "messaged"
        clock = _clock(record.get("at"))
        if str(record.get("via", "")).startswith("whatsapp cloud"):
            what = "I told them you are busy and asked what to pass on"
        else:
            what = ("the busy reply was written, not sent - no WhatsApp token is "
                    "configured")
        self._tell(f"{record.get('who', 'someone')} {verb}"
                   f"{f' at {clock}' if clock else ''} - {what}", record)

    def _tell(self, message: str, record: dict, fact: str = "") -> None:
        """One notice: on screen, in this session's memory, and settled on file.

        ``fact`` is what ``:status`` remembers when it differs from the notice: a
        second message is announced as itself but remembered as everything that
        caller is on file for, so the dashboard groups them the way the report
        does. The notice itself is never grouped - it reports what just happened.
        """
        self._notice(message)
        self._remember(fact or message,
                       who=str(record.get("wa_id") or record.get("who") or ""))
        self._mark_reported(record)

    def _caller_fact(self, record: dict) -> str:
        """Everything one caller is on file for, as the report would phrase it."""
        wa_id = str(record.get("wa_id") or "")
        if not wa_id:
            return _relay_line(record)
        with self._lock:
            mine = [r for r in self.load().get("relays", [])
                    if str(r.get("wa_id")) == wa_id]
        return _caller_line(mine or [record])

    def _mark_reported(self, record: dict) -> None:
        """Settle one caller's entries: told live, so the next start is quiet."""
        key = record.get("key")
        if not key:
            return
        with self._lock:
            state = self.load()
            for item in (state.get("relays", []) + state.get("rings", [])
                         + state.get("failed", [])):
                if item.get("key") == key:
                    item["reported"] = True
            self.save(state)

    def _remember(self, line: str, who: str = "") -> None:
        """Keep what the user has been told this session, for ``:status``."""
        text = " ".join(str(line or "").split())
        if not text:
            return
        with self._lock:
            self._told[who or f"+{len(self._told)}"] = text
            while len(self._told) > _MAX_SESSION:
                self._told.pop(next(iter(self._told)))

    def session_report(self) -> str:
        """What this session has already told you - the ``:status`` dashboard."""
        with self._lock:
            return "; ".join(self._told.values())

    @staticmethod
    def _notice(message: str) -> None:
        """Post one line where a running app shows it: console + browser alert.

        ``log.proactive`` is the surface the proactive daemon already uses, and
        the browser worker wraps it, so one call reaches both.
        """
        log.proactive(rule_name="WhatsApp", message=message,
                      title="WhatsApp while you were away",
                      event_type="whatsapp_away")

    # ---- the local driver -------------------------------------------------- #

    def driver(self) -> "LocalBridge":
        """A handle that feeds the loop the way a webhook would."""
        return LocalBridge(self)


class LocalBridge:
    """Drives the loop without Meta: appends the payloads a webhook would append.

    This is the inbound path you can actually use today. It writes the same Cloud
    API shapes Meta sends (a Call Connect for a ringing call, a message for a
    reply), so the real parse path runs end to end - the bridge stands in for the
    transport, never for the logic.
    """

    def __init__(self, away: Away) -> None:
        self._away = away

    def _append(self, payload: dict) -> None:
        path = self._away.inbox_path()
        if path is None:
            raise RuntimeError("set WHATSAPP_INBOX to the JSONL file the driver "
                               "and the webhook share")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(payload) + "\n")

    def caller_dials(self, wa_id: str, name: str = "") -> None:
        """An inbound call - the Call Connect webhook Meta sends when you ring."""
        stamp = int(time.time())
        self._append(_webhook(
            "calls", [_contact(wa_id, name)],
            [{"id": f"wacid.{stamp}{os.urandom(2).hex()}", "from": wa_id,
              "event": "connect", "timestamp": str(stamp)}]))

    def caller_says(self, wa_id: str, text: str, name: str = "") -> None:
        """An inbound message, as the messages webhook delivers it."""
        stamp = int(time.time())
        self._append(_webhook(
            "messages", [_contact(wa_id, name)],
            [{"from": wa_id, "id": f"wamid.{stamp}{os.urandom(2).hex()}",
              "timestamp": str(stamp), "type": "text", "text": {"body": text}}]))

    def replies(self) -> list[dict]:
        """What the loop tried to send while no token was configured."""
        try:
            lines = self._away.outbox_path.read_text(
                encoding="utf-8").splitlines()
        except Exception:
            return []
        out: list[dict] = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out


def _contact(wa_id: str, name: str) -> dict:
    contact: dict = {"wa_id": wa_id}
    if name:
        contact["profile"] = {"name": name}
    return contact


def _webhook(field: str, contacts: list, items: list) -> dict:
    """A webhook body in Meta's shape, for whichever field carries the event."""
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "0", "changes": [{
            "field": field,
            "value": {"messaging_product": "whatsapp",
                      "metadata": {"phone_number_id": "0", "display_phone_number": "0"},
                      "contacts": contacts,
                      field: items}}]}],
    }


def _prompt(ev: dict, heard: str = "") -> str:
    """The caller's side of the conversation, handed to the model."""
    called = ev.get("kind") == "call"
    lines = [f"Caller: {_who(ev)} (WhatsApp {ev.get('sender') or 'unknown number'})",
             f"They {'called' if called else 'messaged'}"]
    clock = _clock(ev.get("ts"))
    if clock:
        lines[-1] += f" at {clock}"
    lines[-1] += "."
    if heard:
        lines.append(f'What they said: "{heard}"')
    elif called:
        lines.append("They sent no text - this was a call that rang.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# what the application calls
# --------------------------------------------------------------------------- #

_STORE: Optional[Away] = None


def away_store() -> Away:
    """The store the application uses; tests build their own with explicit paths."""
    global _STORE
    if _STORE is None:
        _STORE = Away()
    return _STORE


def poll_and_answer(cfg: Any = None, brain: Any = None,
                    away: Optional[Away] = None,
                    limit: int = MAX_PER_PASS, live: bool = False) -> str:
    """Answer everyone waiting, and say in plain words what happened.

    The whole loop, and the one entry point both callers use: whatever reached the
    inbox since last time is answered in the brain's own words, and when the
    caller replied, that reply is captured for the report. Returns ``""`` when
    nobody was waiting, so a quiet start stays quiet.

    ``live`` - what the running application's :class:`LiveWatch` passes - announces
    each caller the moment they answer, on both surfaces, and marks it reported so
    the next briefing does not repeat it. Left off, the capture waits for the
    startup briefing instead.
    """
    store = away or away_store()
    # A save that could not land left the state in a spill; put it back now that
    # the file is free again, whether or not anybody is waiting.
    try:
        store.recover()
    except Exception as exc:                 # never let housekeeping stop a pass
        log.debug(f"away: could not fold the spill back: {exc}")
    events = store.events()
    if not events:
        return ""
    if brain is None:
        if cfg is None:
            return ""
        from .agent.brain import make_brain

        brain = make_brain(cfg.brain)

    answered: list[tuple[str, Reply]] = []
    captured: list[dict] = []
    missed: list[dict] = []
    for ev in events[:limit]:
        outcome = store.handle(ev, brain)
        if outcome.relay is not None:
            captured.append(outcome.relay)
        if outcome.reply is not None and outcome.reply.delivered:
            answered.append((_who(ev), outcome.reply))
        if outcome.failure is not None and outcome.relay is None:
            missed.append(outcome.failure)
        if live:
            # Announce now. One notice per caller: what they said, or - if they
            # could not be reached at all - that they could not be reached. A
            # caller who has only just been asked gets the shorter notice, so a
            # ring that never turns into a message is still not invisible.
            record = outcome.relay or outcome.failure
            if record is not None:
                store.announce(record, unanswered=outcome.relay is None)
            elif outcome.asked is not None:
                store.announce_ask(outcome.asked)

    parts = []
    if answered:
        parts.append("replied to " + ", ".join(who for who, _ in answered))
    if captured:
        parts.append("took a message from " + ", ".join(
            str(r.get("who", "someone")) for r in captured))
    if missed:
        parts.append("could not reach " + ", ".join(
            str(r.get("who", "someone")) for r in missed)
            + " - see the report for the reason")
    if not parts:
        return ""                # nothing was owed, so a quiet start stays quiet
    bridges = [r for _, r in answered if r.via == "local bridge"]
    if bridges:
        parts.append(f"via the local bridge - no WhatsApp token is configured, so "
                     f"nothing was sent from this machine (replies are in "
                     f"{store.outbox_path})")
    return "whatsapp away: " + "; ".join(parts)


def report(mark: bool = False, away: Optional[Away] = None) -> str:
    """What callers left word about, for the console's own surfaces."""
    return (away or away_store()).report(mark=mark)


def session_report(away: Optional[Away] = None) -> str:
    """What this session has already told you, for the ``:status`` dashboard.

    The greeting consumes the report, so without this the dashboard would be empty
    the moment the user looked at it, and a caller announced live would never
    appear there at all.
    """
    return (away or away_store()).session_report()


def driver(away: Optional[Away] = None) -> LocalBridge:
    """The local driver: ring the assistant, or have a caller reply."""
    return (away or away_store()).driver()


# --------------------------------------------------------------------------- #
# while the application is running
# --------------------------------------------------------------------------- #

class LiveWatch:
    """Answers callers while the application is open, and says so as it happens.

    :func:`poll_and_answer` at startup only serves whoever arrived while the app
    was closed. A caller who writes while Jarvis is running needs the same
    treatment *now*, so the console starts this: one thread, one poll of the inbox
    every :data:`LIVE_INTERVAL` seconds, through that same function - same brain,
    same seam, same store - with ``live=True`` so each caller is announced as they
    answer rather than at the next start.

    The store's lock is held across each caller, so the startup pass and this
    watcher can never both ask the same person the same question.
    """

    def __init__(self, cfg: Any = None, brain: Any = None,
                 away: Optional[Away] = None,
                 interval: float = LIVE_INTERVAL) -> None:
        self.cfg = cfg
        self.brain = brain
        self.away = away or away_store()
        self.interval = interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """Begin watching; ``False`` when there is nothing to watch or it is on.

        With no inbound file configured there is no source to poll, so no thread
        is started at all rather than one that wakes every two seconds to find
        nothing. The location is asked of the store, which asks the connectors.
        """
        if self.running or self.away.inbox_path() is None:
            return False
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="jarvis-whatsapp-away")
        self._thread.start()
        return True

    def stop(self, timeout: float = 1.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=timeout)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                poll_and_answer(self.cfg, self.brain, away=self.away, live=True)
            except Exception as exc:      # a watcher must never take the app down
                log.debug(f"away watch: {exc}")


_WATCH: Optional[LiveWatch] = None


def start_watch(cfg: Any = None, brain: Any = None,
                away: Optional[Away] = None) -> bool:
    """Watch for callers while the application runs; ``False`` when nothing to watch."""
    global _WATCH
    if _WATCH is None or not _WATCH.running:
        _WATCH = LiveWatch(cfg, brain, away)
    return _WATCH.start()


def stop_watch() -> None:
    """Stop the live watch, if one was started."""
    global _WATCH
    if _WATCH is not None:
        _WATCH.stop()
        _WATCH = None
