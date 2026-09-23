"""Messaging Jarvis on Discord: the inbound half of the Discord connector.

``jarvis/tools/connectors.py`` already talks to Discord - the servers the bot is
in, the messages in a channel, and (with its ``send`` op) posting one. All of that
is Jarvis acting *on* Discord. This module is the other direction: you write to
Jarvis and it answers, with no console open, no window in front and nothing to
click.

The transport is Discord's gateway WebSocket rather than an HTTP interactions
endpoint, because an endpoint needs a public URL and this program runs on a
laptop. That connection is long-lived, so it runs in **its own process**:

    python -m jarvis.discord_bot

That is not fussiness. Holding a gateway connection inside the console was
measured to wedge the floating HUD's teardown at exit - the main thread ends up
in ``tkinter``'s ``destroy``, waiting on a Tcl interpreter whose thread has
already gone (``jarvis/hud/mini_overlay.py``), and ``:quit`` never returns. A
dumb thread at the same point in startup is harmless; a real gateway connection
is not. A socket that should outlive a window has no business sharing a lifecycle
with one, and a listener that can be started, watched and stopped on its own is
the better shape regardless.

Who gets answered, and what with:

  * **only the allowed.** ``DISCORD_ALLOWED_USERS`` names them, by id or by
    username. With it unset the default is the *application's own owner* - the
    account that created the bot. When Discord will not name an owner the
    listener answers nobody and says so: a bot that cannot name who may drive it
    does not get to guess.
  * **never a bot**, its own messages included, so two bots cannot answer each
    other forever.
  * **direct messages always; a server channel only when the bot is mentioned.**
    ``JARVIS_DISCORD_GUILD=1`` answers in channels too, which is a choice a busy
    server does not want as a default.
  * **it talks, and with ``JARVIS_DISCORD_AGENT=1`` it also acts.** Words come
    from the brain, as the away assistant's do, and a message that reads like a
    command is carried out on this computer by an agent of its own - this process
    has no console to hand it to, and the console's own desktop lock is in-process
    and would not be visible from here anyway. The switch is separate and off by
    default because it is a larger promise than talking: whoever can message this
    bot can move the mouse and keyboard of the machine.

    Acting comes with three properties the listener has to keep on its own:
    **one task at a time**, enforced across processes by a lock file, so a task
    here and a scheduled job on the desk cannot fight over the mouse; **off the
    gateway thread**, since a task takes minutes and the heartbeat that keeps the
    session open has milliseconds; and **questions come back to Discord** - when
    the agent needs to know something it asks in the channel it was messaged from
    and waits for the next message, and an answer that never arrives ends the task
    with the question rather than guessing on your behalf.

Deliberately not here: fetching the messages sent while the application was
closed. Discord keeps them and an unread count would be trivial, but answering a
backlog of instructions as if they had just arrived is its own decision, not a
side effect of reconnecting.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import random
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .utils import logging as log

#: Listening is opt-in, like voice and live voice: a token in the environment is
#: what Jarvis *can* do, not what it should start doing on every launch. It is
#: also the only switch that keeps a test driving the console from opening a
#: websocket to Discord, and the only way to say "do not answer anyone right now"
#: without deleting a working credential.
ENABLE_ENV = "JARVIS_DISCORD_LISTEN"

#: Acting is a second, larger opt-in than talking: with it on, whoever can message
#: the bot can move this computer's mouse and keyboard.
AGENT_ENV = "JARVIS_DISCORD_AGENT"

#: How long the agent's question waits for an answer in Discord before the task
#: gives up. Long, because the person answering walked away from the keyboard -
#: that is the whole reason they are talking to Jarvis on their phone.
ASK_TIMEOUT = 300.0

#: The file that keeps two Jarvis processes off the desktop at once (this
#: listener and a console with a scheduled job are two processes; Jarvis's own
#: lock is per-process). The holder touches it this often, and a file untouched
#: for longer than this is taken to be a wreck rather than a running task.
_LOCK_BEAT = 20.0
_LOCK_STALE = 90.0

#: Discord's endpoint. Overridable so a test - or a self-hosted gateway - never
#: has to reach the internet for this module to run.
GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json"

# The intents to ask for. MESSAGE_CONTENT is a *privileged* intent and the whole
# reason a listener can silently come up connected and answer nothing: without it
# a message arrives with its text replaced by an empty string. GUILD_MESSAGES is
# kept even when channel replies are off, because a mention of the bot *is* a
# guild message.
_GUILDS = 1 << 0
_GUILD_MESSAGES = 1 << 9
_DIRECT_MESSAGES = 1 << 12
_MESSAGE_CONTENT = 1 << 15
INTENTS = _GUILDS | _GUILD_MESSAGES | _DIRECT_MESSAGES | _MESSAGE_CONTENT

#: Close codes a reconnect cannot fix, and what each one means to the user.
_FATAL_CLOSE = {
    4004: "Discord rejected the bot token (4004). Regenerate it in the Developer "
          "Portal and update DISCORD_BOT_TOKEN in .env.",
    4010: "Discord refused the gateway connection (4010) - the shard settings "
          "were rejected.",
    4011: "Discord says the bot needs sharding (4011), which this listener does "
          "not do - it is meant for one desktop, not a fleet.",
    4012: "Discord refused the gateway version (4012).",
    4013: "Discord refused the requested intents (4013) - the number asked for "
          "is not one it will grant.",
    4014: "Discord refused the Message Content intent (4014). Turn it on under "
          "Bot > Privileged Gateway Intents at discord.com/developers/"
          "applications, then restart Jarvis.",
}

#: The longest message Discord accepts; it refuses a longer body outright.
MAX_CHARS = 2000
#: Turns kept per channel, and channels kept, so a long conversation costs a
#: bounded amount of memory and a bounded prompt.
MAX_HISTORY = 12
MAX_CHANNELS = 16
#: Reconnect delays, in seconds. The last one repeats.
_BACKOFF = (1.0, 2.0, 5.0, 15.0, 30.0)

#: Posted when the model returns nothing that can be sent as a message. Silence
#: would leave a broken brain looking like a broken bot, so it says which it is -
#: the same wording the mobile relay falls back to.
_NO_ANSWER = "I could not produce an answer just now. Please try again."

#: What the reply has to achieve, never the words themselves - those come from
#: the model. It is told what it cannot do because a model that is told nothing
#: about its limits invents them.
_DM_SYSTEM = (
    "You are Jarvis, answering a Discord message from the person whose computer "
    "you run on. This is a message, not a task: reply the way you would speak, in "
    "the first person, in one to three short sentences.\n\n"
    "Your answer is posted to Discord as plain text and is usually read on a "
    "phone, so keep it simple: no headings, no code fences, no bullet lists "
    "unless you were asked for a list, no JSON, no emoji. Write in the language "
    "you were written to in.\n\n"
    "You cannot see their screen and you have no tools here. When you are asked "
    "to do something on the computer, say plainly that the console on their desk "
    "does that, and offer what you can answer in words. Invent nothing and "
    "promise nothing."
)

#: Added to the prompt above when this listener does have hands. Without it the
#: model is still told it has no tools, and answers "I cannot do that" about a
#: command it has just been carrying out - the one thing a message and its own
#: result disagreeing about would make the whole feature look broken.
_AGENT_NOTE = (
    "\n\nCorrection to the paragraph above: on this machine you DO act - a message "
    "that asks for something on the computer is carried out by you, and its result "
    "is posted separately. Never say you cannot do something on the computer. "
    "Speak about what you are doing or what the result was, in the first person, "
    "and keep it short."
)

#: A tool call written as markup rather than as this project's JSON envelope -
#: the shape a model produces when it has tools on the brain but none in hand.
#: Two dialects, because both were seen from a real model in one evening: the
#: XML-ish ``<invoke name="system_status">`` and the bracketed
#: ``[TOOL_CALL]{tool => "desktop-commander_start-app"}``, which is what a person
#: got instead of an opened calculator.
_TOOL_MARKUP = re.compile(
    r"<invoke\b|<tool_call\b|</invoke\b|\[TOOL_CALL\]|\{tool\s*=>",
    re.IGNORECASE,
)

#: How long a fast command waits for a window to prove it worked. A cold start is
#: seconds; a name Windows cannot find is an error dialog that never appears.
_FAST_WAIT = 6.0

#: The commands answered with one tool call rather than a model. Measured on the
#: configured provider: a completion costs ~10.5s and a ten-call task 137s, while
#: opening an app is a single call that takes 1.1s. A phone waiting three minutes
#: for a calculator is the whole problem, and these are the messages people send.
#: Only whole messages match - one clause, no "and then" - because anything with
#: more to it is a plan, and plans are the agent's job.
_FAST_OPEN = re.compile(
    r"^(?:open|launch|start|run|bring up)\s+(?:the\s+|my\s+)?"
    r"(?P<what>[^,;:]{2,30}?)(?:\s+(?:app|application|program))?$", re.IGNORECASE)
_FAST_CLOSE = re.compile(
    r"^(?:close|quit|exit)\s+(?:the\s+|my\s+)?"
    r"(?P<what>[^,;:]{2,40}?)(?:\s+(?:window|app|application))?$", re.IGNORECASE)
_FAST_URL = re.compile(
    r"^(?:open|go to|visit)\s+(?P<url>(?:https?://)?(?:[\w-]+\.)+"
    r"(?:com|net|org|io|ai|dev|gg|co|tv|me|app|sh|xyz|edu|gov)(?:/\S*)?)$",
    re.IGNORECASE)
#: Politeness at the *front*, which the router also knows and this path needs too:
#: "can you open calculator" is the same command as "open calculator"
#: punctuation".
_POLITE_HEAD = re.compile(
    r"^(?:(?:can|could|will|would|do)\s+you\s+|please\s+|jarvis[,!\s]+|"
    r"hey[,!\s]+|now\s+)+", re.IGNORECASE)

#: Trailing politeness the console's router does not see past. "can you open
#: calculator FOR ME" is a command, and reading it as conversation is what made a
#: phone message end in ``[TOOL_CALL]{...}`` rather than an open calculator. Only
#: a fallback: the phrase is tried as written first, so nothing that already reads
#: as a task is affected, and the router still has to recognise a verb in what is
#: left - "that is great for me" stays talk.
_POLITE_TAIL = re.compile(
    r"[\s,]+(for me|for us|please|now|thanks|thank you)\.?$", re.IGNORECASE)

_TRUTHY = {"1", "true", "yes", "on"}


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in _TRUTHY


def _entries(value: str) -> list[str]:
    """Split an allowlist into entries, accepting commas, spaces or both."""
    out: list[str] = []
    for chunk in str(value or "").replace(",", " ").split():
        item = chunk.strip().lstrip("@")
        if item and item not in out:
            out.append(item)
    return out


def _plain(text: Any) -> str:
    """One message's worth of prose from a completion, or an empty string.

    **Only a string is prose.** Anything else - ``None``, a dict, or the ``Mock`` a
    stand-in brain hands back - is not an answer, and coercion is how a test double
    ends up talking to a real person: a Mock brain replied to "Hi" with
    ``<Mock name='agent.brain.complete()' id='...'>``, which is a ``repr`` posted to
    a phone. An empty return makes the caller report the failure instead.

    The configured model also speaks the agent loop's JSON action envelope, and a
    Discord message made of one would be unreadable, so a reply hiding inside an
    envelope is unwrapped rather than posted.
    """
    if not isinstance(text, str):
        return ""
    body = " ".join(text.split()).strip().strip('"').strip()
    if _TOOL_MARKUP.search(body):
        # The model reached for a tool, which this half of the listener does not
        # have. Its call syntax is not prose: posting it puts
        # ``<invoke name="system_status">`` on someone's phone. Read as a failure,
        # and the caller says so out loud while the log keeps the reason.
        return ""
    if body.startswith("{"):
        try:
            data = json.loads(body)
        except Exception:
            return ""
        body = ""
        if isinstance(data, dict):
            for name in ("message", "reply", "text", "body", "answer"):
                value = data.get(name)
                if isinstance(value, str) and value.strip():
                    body = " ".join(value.split())
                    break
        if not body:
            return ""
    return body


def _split(text: str, limit: int = MAX_CHARS) -> list[str]:
    """Break a reply into messages Discord will accept.

    On paragraph, then line, then word boundaries, in that order, because a
    Discord message cut mid-sentence reads as a bug.
    """
    body = str(text or "").strip()
    if not body:
        return []
    if len(body) <= limit:
        return [body]
    chunks: list[str] = []
    for paragraph in body.split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= limit:
            chunks.append(paragraph)
            continue
        line = ""
        for word in paragraph.split():
            while len(word) > limit:
                # A single unbroken run longer than a message, a URL usually.
                # It is cut, but every character of it is still sent.
                if line:
                    chunks.append(line)
                    line = ""
                chunks.append(word[:limit])
                word = word[limit:]
            candidate = f"{line} {word}".strip()
            if len(candidate) > limit:
                if line:
                    chunks.append(line)
                line = word
            else:
                line = candidate
        if line:
            chunks.append(line)
    # Fold anything still over the limit, and join the pieces back into a shape
    # Discord shows as consecutive messages.
    out: list[str] = []
    for chunk in chunks:
        while len(chunk) > limit:
            out.append(chunk[:limit])
            chunk = chunk[limit:]
        out.append(chunk)
    merged: list[str] = []
    for chunk in out:
        if merged and len(merged[-1]) + len(chunk) + 2 <= limit:
            merged[-1] = f"{merged[-1]}\n\n{chunk}"
        else:
            merged.append(chunk)
    return merged


class DiscordBot:
    """The gateway connection and the conversation policy in one place.

    Everything that talks to the network is injectable - the gateway URL, the
    outgoing sender and the owner lookup - so the policy above (who is answered,
    with what, and what is refused) is testable without a socket.
    """

    def __init__(self, cfg: Any = None, brain: Any = None,
                 runner: Optional[Callable[[str], str]] = None, *,
                 token: str = "", allow: Optional[list[str]] = None,
                 gateway: str = "", send: Optional[Callable[..., str]] = None,
                 owner_lookup: Optional[Callable[[], str]] = None) -> None:
        self.cfg = cfg
        self.brain = brain
        #: A ``runner(command) -> result`` puts the agent loop behind Discord.
        #: Without one Jarvis only ever talks here.
        self.runner = runner
        self._token = token
        self._allow = list(allow) if allow is not None else None
        self._gateway = gateway or GATEWAY_URL
        self._send = send
        self._owner_lookup = owner_lookup

        self.bot_id = ""
        self.last_error = ""
        self.answered = 0
        self._allowed: Optional[frozenset] = None
        self._allowed_error = ""
        self._handled: set[str] = set()
        self._history: dict[str, list[dict]] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        #: The message being served right now, so the agent's questions can be
        #: asked in the channel they came from.
        self._turn: Optional[dict] = None
        #: channel id -> the question a task is waiting on an answer to.
        self._pending: dict[str, dict] = {}
        self._turn_lock = threading.Lock()
        #: How many commands have been carried out here, as opposed to answered.
        self.tasks = 0
        #: The sequence number every heartbeat has to echo back.
        self._seq: Optional[int] = None

    # ---- what it needs to work -------------------------------------------- #

    def token(self) -> str:
        return (self._token or os.getenv("DISCORD_BOT_TOKEN", "")).strip()

    def allow_entries(self) -> list[str]:
        """Who may be answered: the explicit list, else the environment."""
        if self._allow is not None:
            return list(self._allow)
        return _entries(os.getenv("DISCORD_ALLOWED_USERS", ""))

    def allowed(self) -> frozenset[str]:
        """The allowlist, resolved once and remembered.

        Order matters: an explicit list wins, then the environment, then - with
        neither - the application's own owner. Nothing here falls back to
        "everyone": a public bot invited to a server is exactly the case that
        makes an empty allowlist dangerous rather than permissive.
        """
        if self._allowed is not None:
            return self._allowed
        # An '@' and a capital letter are how a person writes a username; the
        # matcher below compares neither, so they come off here - for a list
        # handed in by a caller as much as for one read out of the environment.
        entries = [e.strip().lstrip("@").lower() for e in self.allow_entries()]
        entries = [e for e in entries if e]
        if not entries:
            lookup = self._owner_lookup
            if lookup is None:
                from .tools import connectors
                lookup = connectors.discord_owner_id
            try:
                owner = str(lookup() or "").strip().lower()
            except Exception as exc:
                owner = ""
                self._allowed_error = " ".join(str(exc).split())[:200]
            if owner:
                entries = [owner]
        if entries:
            # Only a *successful* answer is remembered. A lookup that failed is
            # retried on the next message rather than switching the listener off
            # for the rest of the session over one bad moment on the network.
            self._allowed = frozenset(entries)
        return frozenset(entries)

    def why_not(self, need_brain: bool = True) -> str:
        """``""`` when it can answer, else why it cannot - for the user to read.

        ``need_brain=False`` asks only the questions about configuration, so a
        caller can report "listening is switched off" before building a model
        client it would then have nothing to do with.
        """
        if not _truthy(ENABLE_ENV):
            return (f"Jarvis is not set to listen on Discord. Set {ENABLE_ENV}=1 "
                    "in .env, along with DISCORD_BOT_TOKEN, to answer messages "
                    "sent to the bot.")
        if not self.token():
            return ("DISCORD_BOT_TOKEN is not set, so Jarvis cannot log in to "
                    "Discord. Copy the bot token from discord.com/developers/"
                    "applications into .env.")
        if not self.allowed():
            detail = (f" ({self._allowed_error})" if self._allowed_error else "")
            return ("no one may be answered yet: set DISCORD_ALLOWED_USERS to your "
                    "Discord user id, and Discord did not name the application "
                    "owner" + detail)
        if need_brain and self.brain is None and self.runner is None:
            return "no brain is available to answer with"
        return ""

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status(self) -> str:
        """One line for the console: whether it listens, and as whom."""
        if not _truthy(ENABLE_ENV):
            return f"discord: off - not enabled (set {ENABLE_ENV}=1 in .env)"
        why = self.why_not()
        if why:
            return f"discord: off - {why}"
        if not self.running:
            return "discord: not listening (idle)"
        who = ", ".join(sorted(self.allowed()))
        return f"discord: listening as {self.bot_id or 'the bot'}, answering {who}"

    # ---- lifecycle --------------------------------------------------------- #

    def start(self) -> bool:
        """Hold the gateway open on a daemon thread; ``False`` when it cannot."""
        if self.running:
            return False
        why = self.why_not()
        if why:
            self.last_error = why
            # A listener that was never asked for is not a warning; one that was
            # asked for and cannot work is.
            (log.info if not _truthy(ENABLE_ENV) else log.warn)(f"Discord: {why}")
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._thread_main, daemon=True,
                                        name="jarvis-discord")
        self._thread.start()
        return True

    def stop(self, timeout: float = 5.0) -> None:
        """Ask the listener to leave, and wait for it to.

        The thread closes its socket and returns within a fifth of a second, so
        the join finishes rather than timing out: a bot still holding a session
        open would go on showing as online after Jarvis had quit.
        """
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=timeout)

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._gateway_loop())
        except asyncio.CancelledError:
            pass                            # asked to stop mid-frame; that is not a failure
        except Exception as exc:            # a listener must never take the app down
            self.last_error = " ".join(str(exc).split())[:200]
            log.debug(f"discord: listener stopped: {exc}")

    # ---- the connection ---------------------------------------------------- #

    async def _sleep(self, seconds: float) -> None:
        """Sleep, but wake as soon as :meth:`stop` is asked for."""
        waited = 0.0
        while waited < seconds and not self._stop.is_set():
            await asyncio.sleep(min(0.2, seconds - waited))
            waited += 0.2

    async def _gateway_loop(self) -> None:
        # A clean session that ends straight away still reconnects, but a session
        # that ran for a while starts again at the shortest delay: the backoff
        # exists for a gateway that is refusing us, not for one that restarted.
        attempt = 0
        while not self._stop.is_set():
            started = asyncio.get_running_loop().time()
            code = None
            try:
                code = await self._session()
            except Exception as exc:
                self.last_error = " ".join(str(exc).split())[:200]
                log.debug(f"discord: gateway connection ended: {exc}")
            if self._stop.is_set():
                return
            if code in _FATAL_CLOSE:
                self.last_error = _FATAL_CLOSE[code]
                log.warn(f"Discord: {self.last_error}")
                return
            if asyncio.get_running_loop().time() - started > 30:
                attempt = 0
            delay = _BACKOFF[min(attempt, len(_BACKOFF) - 1)]
            attempt += 1
            await self._sleep(delay * (0.8 + 0.4 * random.random()))

    async def _session(self) -> Optional[int]:
        """One gateway session; returns the close code when Discord sent one."""
        import websockets                       # imported late: importing this
                                                # module should stay cheap

        async with websockets.connect(self._gateway, max_size=None,
                                      ping_interval=None) as ws:
            hello = json.loads(await asyncio.wait_for(ws.recv(), 30))
            interval = float((hello.get("d") or {}).get("heartbeat_interval")
                             or 45_000) / 1000.0
            beat = asyncio.create_task(self._heartbeat(ws, interval))
            try:
                await ws.send(json.dumps({"op": 2, "d": {
                    "token": self.token(),
                    "intents": INTENTS,
                    "properties": {"os": os.name, "browser": "jarvis",
                                   "device": "jarvis"},
                }}))
                await self._pump(ws)
            except Exception as exc:
                return _close_code(exc)
            finally:
                beat.cancel()
        return None

    async def _pump(self, ws) -> None:
        """Read frames until Discord closes, or until Jarvis is asked to stop.

        The two are raced explicitly rather than using ``async for``, because that
        would wait on the socket forever: a listener that cannot be asked to leave
        would keep the bot showing as online after Jarvis had quit, and would still
        be running while the rest of the application tears itself down.
        """
        reader = asyncio.create_task(self._read_frames(ws))
        try:
            while not reader.done():
                if self._stop.is_set():
                    reader.cancel()
                    break
                await asyncio.sleep(0.2)
        finally:
            if not reader.done():
                reader.cancel()
            # CancelledError is a BaseException, so suppressing Exception alone
            # would let the cancellation escape and print a traceback on every
            # quit - the listener being asked to leave is not an error.
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await reader

    async def _read_frames(self, ws) -> None:
        async for raw in ws:
            try:
                frame = json.loads(raw)
            except Exception:
                continue
            if frame.get("op") == 1:
                # Discord asking for a beat this instant; answering it from here
                # is the only place the socket is in hand.
                await ws.send(json.dumps({"op": 1, "d": self._seq}))
                continue
            await self._frame(frame)

    async def _heartbeat(self, ws, interval: float) -> None:
        """Keep the session alive; Discord drops it within a minute otherwise."""
        while True:
            await asyncio.sleep(interval)
            try:
                await ws.send(json.dumps({"op": 1, "d": self._seq}))
            except Exception:
                return

    async def _frame(self, frame: dict) -> None:
        op = frame.get("op")
        if isinstance(frame.get("s"), int):
            self._seq = frame["s"]          # every heartbeat echoes this back
        if op == 7:
            raise RuntimeError("gateway asked for a reconnect")
        if op == 9:
            raise RuntimeError("gateway refused the session (op 9)")
        if op == 0:
            event = str(frame.get("t") or "")
            data = frame.get("d") or {}
            if event == "MESSAGE_CREATE":
                # Off the event loop, because answering costs a model round trip
                # and a command costs minutes while the heartbeat that holds the
                # session open has milliseconds: on the loop, one command would
                # drop the connection that is supposed to deliver its result.
                await asyncio.to_thread(self._dispatch, event, data)
                return
            self._dispatch(event, data)

    def _dispatch(self, event: str, data: dict) -> None:
        if event == "READY":
            user = data.get("user") or {}
            self.bot_id = str(user.get("id") or "")
            who = ", ".join(sorted(self.allowed())) or "nobody"
            log.ok(f"Discord: listening as {user.get('username') or 'the bot'} - "
                   f"answering {who}"
                   + ("" if _truthy("JARVIS_DISCORD_GUILD")
                      else " (direct messages, and mentions in a server)"))
        elif event == "MESSAGE_CREATE":
            self.handle_message(data)

    # ---- the conversation -------------------------------------------------- #

    def handle_message(self, message: dict) -> str:
        """Answer one gateway message; returns the reply posted, or ``""``.

        The whole policy of the module is behind this call - what is ignored, who
        is refused, what the reply says and where it goes - so the behaviour can
        be pinned without a socket in sight.
        """
        text, reason = self._incoming(message)
        if text is None:
            log.debug(f"discord: ignored a message ({reason})")
            return ""
        if self._deliver_answer(message, text):
            return ""
        try:
            reply = self._compose(message, text)
        except Exception as exc:
            self.last_error = " ".join(str(exc).split())[:200]
            log.warn(f"Discord: could not answer that message: {exc}")
            return ""
        if not reply:
            # Nothing that can be posted is a failure worth saying out loud: the
            # person waiting gets an honest line rather than silence, and the
            # reason - a stand-in brain, or a model that answered with nothing -
            # is in this process's log where the operator can see it.
            self.last_error = ("the model returned nothing that can be sent as a "
                               "message")
            log.warn("Discord: " + self.last_error + " - check the configured "
                     "brain (nothing was taken from the completion)")
            reply = _NO_ANSWER
        try:
            self._post(message, reply)
        except Exception as exc:
            self.last_error = " ".join(str(exc).split())[:200]
            log.warn(f"Discord: could not send the reply: {exc}")
            return ""
        self.answered += 1
        return reply

    def _incoming(self, message: dict) -> tuple[Optional[str], str]:
        """The text to answer, or ``(None, why not)``.

        A message that gets this far is marked handled before it is answered, so
        Discord redelivering the same event cannot produce a second reply.
        """
        mid = str(message.get("id") or "")
        if not mid:
            return None, "no message id"
        if mid in self._handled:
            return None, "already answered"
        author = message.get("author") or {}
        if not isinstance(author, dict):
            return None, "no author"
        if author.get("bot"):
            return None, "a bot wrote it"
        author_id = str(author.get("id") or "")
        if not author_id:
            return None, "no author id"
        if self.bot_id and author_id == self.bot_id:
            return None, "its own message"
        if not self._is_allowed(author, author_id):
            return None, f"{author_id} is not on the allowlist"

        text = str(message.get("content") or "").strip()
        in_guild = bool(message.get("guild_id"))
        if in_guild:
            unmentioned = self._mentioned(text) if self.bot_id else ""
            if not unmentioned and not _truthy("JARVIS_DISCORD_GUILD"):
                return None, "a server message that does not mention the bot"
            text = unmentioned or text
        if not text:
            return None, "no text (is the Message Content intent on?)"

        self._handled.add(mid)
        if len(self._handled) > 2000:
            self._handled = set(list(self._handled)[-1000:])
        return text, ""

    def _is_allowed(self, author: dict, author_id: str) -> bool:
        """An id, or a username, from the allowlist.

        Usernames are accepted because an id is not a thing anyone knows by
        heart, and matched case-insensitively against the name and the display
        name - one of the three is what the user sees in their own client.
        """
        allowed = self.allowed()
        if not allowed:
            return False
        if author_id.lower() in allowed:
            return True
        for key in ("username", "global_name"):
            name = str(author.get(key) or "").strip().lower()
            if name and name in allowed:
                return True
        return False

    @staticmethod
    def _mentioned(text: str) -> str:
        """The text with a mention of the bot removed, or ``""`` when absent."""
        import re
        if not text:
            return ""
        stripped = re.sub(r"<@!?(\d+)>", "", text).strip()
        return stripped if stripped != text.strip() else ""

    def _deliver_answer(self, message: dict, text: str) -> bool:
        """Hand a message to a task that is waiting on one, instead of answering it.

        Someone being *asked* something mid-task is not starting a second task and
        not making conversation: the next thing they say is the answer. **The
        next** - and only the next. Taking every message while a question is open
        would let a second one overwrite the answer the task is already reading,
        and swallow a real instruction into a question that had been answered.
        Returns whether the message was taken.
        """
        channel = str(message.get("channel_id") or "")
        with self._turn_lock:
            box = self._pending.get(channel)
            if box is None or box.get("done"):
                return False
            box["done"] = True
            box["answer"] = text
            box["event"].set()
        log.info("Discord: taking that as the answer to the question I asked.")
        return True

    def ask_question(self, question: str) -> str:
        """Put the agent's question to the person on Discord and wait for them.

        A hand-off between two threads: the task runs on a worker, the gateway is
        read on the event loop, so the question goes out here and the answer is
        dropped in by ``handle_message``. No answer ends the task with the
        question - the agent loop's own rule for a missing user, and the safe one,
        since nothing is then confirmed on their behalf.
        """
        turn = self._turn
        channel = str((turn or {}).get("channel_id") or "")
        if not channel or turn is None:
            log.warn("Discord: the agent asked something with nobody to ask.")
            return ""
        box: dict = {"event": threading.Event(), "answer": "", "done": False}
        with self._turn_lock:
            if channel in self._pending:    # one question per channel at a time
                return ""
            self._pending[channel] = box
        try:
            self._post(turn, f"❓ {question}")
            log.info(f"Discord: asked something - waiting up to "
                     f"{ASK_TIMEOUT:.0f}s for an answer.")
            box["event"].wait(ASK_TIMEOUT)
        except Exception as exc:
            log.warn(f"Discord: could not ask that question ({exc}).")
        finally:
            with self._turn_lock:
                self._pending.pop(channel, None)
        if not box["answer"]:
            log.warn("Discord: no answer came; the task stops where it asked.")
        return box["answer"]

    def _task_mode(self) -> bool:
        """Whether this listener acts, rather than only talking."""
        return self.runner is not None and _truthy(AGENT_ENV)

    def _compose(self, message: dict, text: str) -> str:
        """The reply: the agent's when it was asked to act, else the brain's.

        A command is recognised twice over, because the first test is a narrow
        router and the second is the model's own behaviour. When the model answers
        a message by *reaching for a tool*, it has said "this is an action" more
        clearly than any list of verbs - and the honest response is to go and do
        it, not to post the call syntax or apologise for having no answer. Both
        are gated on task mode; with it off, Jarvis still only talks.
        """
        if not self._task_mode():
            return self._compose_chat(message, text)[0]
        if self._looks_like_task(text):
            return self._compose_task(message, text)
        reply, raw = self._compose_chat(message, text)
        if _TOOL_MARKUP.search(raw):
            log.info("Discord: the model reached for a tool rather than answering "
                     "- taking that as the command it clearly is.")
            return self._compose_task(message, text)
        return reply

    def _looks_like_task(self, text: str) -> bool:
        """Whether a message is a command, asked of whoever handed the runner over.

        Only the agent knows this (it is the console's own router), and a runner
        without the answer - a plain callable, as a test hands over - is taken to
        mean yes: the caller already decided this listener acts. A router that
        *fails* means no, deliberately: when it is unclear, Jarvis talks instead of
        moving the mouse, which is the harmless way to be wrong.

        The phrase is offered twice when the first answer is no: as written, then
        with trailing politeness removed. The router recognises *wrappers* at the
        front ("can you", "please") but nothing after the object, so "can you open
        calculator for me" is a command it answers no to - and the person who
        wrote it is looking at a phone, not at the console for a different
        phrasing.
        """
        looks = getattr(self.runner, "looks_like_task", None)
        if looks is None:
            return True
        try:
            if looks(text):
                return True
            trimmed = text.strip()
            for _ in range(3):
                shrunk = _POLITE_TAIL.sub("", trimmed).strip()
                if shrunk == trimmed:
                    break
                trimmed = shrunk
                if trimmed and looks(trimmed):
                    return True
            return False
        except Exception as exc:
            log.warn(f"Discord: could not tell a command from talk ({exc}); "
                     "answering it in words.")
            return False

    def _compose_task(self, message: dict, text: str) -> str:
        """Carry out one command, on the desktop, and report what happened.

        The acknowledgement goes out before the work starts, so the person who
        walked away from their laptop sees the command land instead of a silence
        they cannot tell from a broken bot.
        """
        self._turn = message
        try:
            self._post(message, f"🛠️ On it: {text[:200]}")
            result = self.runner(text)
        finally:
            self._turn = None
        self.tasks += 1
        return _plain(result)

    def _compose_chat(self, message: dict, text: str) -> tuple[str, str]:
        """(what to post, the raw completion) - the raw one is the only place a
        tool call is visible: ``_plain`` drops markup, and dropping it silently is
        how a person ends up being told there is no answer to something Jarvis can
        simply do.
        """
        channel = str(message.get("channel_id") or "")
        history = self._history.setdefault(channel, [])
        if len(self._history) > MAX_CHANNELS:
            for old in list(self._history)[:-MAX_CHANNELS]:
                self._history.pop(old, None)
        messages = history[-(MAX_HISTORY - 1):] + [{"role": "user", "content": text}]
        system = _DM_SYSTEM + (_AGENT_NOTE if self._task_mode() else "")
        raw = self.brain.complete(system, messages)
        reply = _plain(raw)
        if reply:
            history.append({"role": "user", "content": text})
            history.append({"role": "assistant", "content": reply})
            del history[:-MAX_HISTORY]
        return reply, (raw if isinstance(raw, str) else "")

    def _post(self, message: dict, reply: str) -> None:
        """Send the reply, replying to their message so a phone shows the thread."""
        send = self._send
        if send is None:
            from .tools import connectors
            send = connectors.discord_send
        channel = str(message.get("channel_id") or "")
        parts = _split(reply)
        for index, part in enumerate(parts):
            send(channel, part,
                 reply_to=str(message.get("id") or "") if index == 0 else "")


def _close_code(exc: BaseException) -> Optional[int]:
    """The close code inside a websockets exception, however it is spelled."""
    for attr in ("code", "rcvd", "sent"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
        code = getattr(value, "code", None)
        if isinstance(code, int):
            return code
    return None


# --------------------------------------------------------------------------- #
# while the application is running
# --------------------------------------------------------------------------- #

_BOT: Optional[DiscordBot] = None


def bot() -> DiscordBot:
    """The one listener this process owns."""
    global _BOT
    if _BOT is None:
        _BOT = DiscordBot()
    return _BOT


def start_bot(cfg: Any = None, brain: Any = None,
              runner: Optional[Callable[[str], str]] = None) -> bool:
    """Listen for Discord messages while the application runs.

    ``False`` when there is nothing to listen with - no token, nobody allowed -
    so a caller can stay quiet instead of reporting a listener that is not there.
    """
    global _BOT
    if _BOT is None or not _BOT.running:
        _BOT = DiscordBot(cfg=cfg, brain=brain, runner=runner)
    return _BOT.start()


def stop_bot() -> None:
    """Stop the listener, if one was started."""
    global _BOT
    if _BOT is not None:
        _BOT.stop()
        _BOT = None


# --------------------------------------------------------------------------- #
# its own process
# --------------------------------------------------------------------------- #

def _task_lock_path() -> Path:
    """The file that means "the mouse is in use", shared by every Jarvis."""
    from .utils.paths import state_root

    return state_root() / "discord_task.lock"


def _lock_is_fresh(path: Path) -> bool:
    """Whether the lock file was touched recently enough to believe it."""
    try:
        return (time.time() - path.stat().st_mtime) < _LOCK_STALE
    except Exception:
        return False


def _claim_lock(path: Path) -> bool:
    """Create the lock file, stealing it when its owner stopped breathing.

    A task that dies without releasing leaves the file behind; an owner that
    cannot be trusted to have gone is worse than a stolen lock, so a file whose
    heartbeat stopped long ago is taken instead of refusing every task forever.
    """
    for _ in range(2):
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if _lock_is_fresh(path):
                return False
            if time.time() - path.stat().st_mtime < _LOCK_STALE:
                return False          # it came back while we looked
            with contextlib.suppress(Exception):
                path.unlink()
            continue
        except Exception:
            # No state directory, no permissions: better to run the task than to
            # refuse it over a bookkeeping file nobody can create.
            return True
        with contextlib.suppress(Exception):
            os.write(fd, f"{os.getpid()} {time.time():.0f}".encode())
        os.close(fd)
        return True
    return False


@contextlib.contextmanager
def desktop_task():
    """The desktop for one task, or a ``False`` when another Jarvis holds it.

    Jarvis's own lock (``scheduler.desktop()``) is in-process, and a console with
    a scheduled job running is another process entirely, so the two would fight
    over the mouse and neither would know. This is a file both of them can see.
    """
    path = _task_lock_path()
    with contextlib.suppress(Exception):
        path.parent.mkdir(parents=True, exist_ok=True)
    if not _claim_lock(path):
        yield False
        return
    stop = threading.Event()

    def _beat() -> None:
        while not stop.wait(_LOCK_BEAT):
            with contextlib.suppress(Exception):
                os.utime(path, None)

    thread = threading.Thread(target=_beat, name="discord-task-lock", daemon=True)
    thread.start()
    try:
        yield True
    finally:
        stop.set()
        with contextlib.suppress(Exception):
            path.unlink()


class _DesktopTools:
    """The tool layer the fast path drives, imported only when it is used.

    These are the same functions the agent's handlers call - ``apps.open_app`` is
    what ``open_app`` is - so a fast command and a thought-out one move the
    computer the same way.
    """

    def open_app(self, name: str) -> str:
        from .tools import apps

        return apps.open_app(name)

    def close_window(self, title: str) -> str:
        from .tools import apps

        return apps.close_window(title)

    def open_url(self, url: str) -> str:
        from .tools import system

        return system.open_url(url)

    def list_windows(self) -> list[str]:
        from .tools import apps

        return apps.list_windows()

    def known_apps(self) -> set[str]:
        """The names safe to launch by name, because a table already says so.

        Deliberately not "anything that looks like an app": a name that is not in
        here could be a file, a folder or a sentence, and ``start`` does not
        report back whether Windows found it - ``open_app("bogus-app-xyz")``
        answers "launched 'bogus-app-xyz'" with an error dialog on the screen.
        """
        from .tools import apps

        return set(apps._KNOWN)


class DiscordRunner:
    """The desktop agent as Discord calls it: one task at a time, on the hook.

    It owns the lock rather than the bot, so the promise "a command moves the
    mouse" has exactly one door in and a test can hand it a different one.
    """

    def __init__(self, agent: Any, asker: Optional[Callable[[str], str]] = None,
                 lock: Optional[Callable[[], Any]] = None,
                 tools: Optional[Any] = None,
                 wait: float = _FAST_WAIT) -> None:
        self.agent = agent
        self.asker = asker
        self._lock = lock or desktop_task
        self._tools = tools or _DesktopTools()
        self._wait = wait
        #: Commands refused because something else already had the desktop.
        self.refused = 0
        #: Commands answered without a model, and how long that took.
        self.fast = 0
        self.fast_seconds = 0.0

    def looks_like_task(self, text: str) -> bool:
        """The console's own router, which is the only thing that knows."""
        return bool(self.agent._looks_like_task(text))

    @staticmethod
    def _bare(text: str) -> str:
        """The command with the politeness taken off both ends."""
        body = text.strip().rstrip(".!").strip()
        for _ in range(3):
            shrunk = _POLITE_HEAD.sub("", body, count=1).strip()
            shrunk = _POLITE_TAIL.sub("", shrunk).strip()
            if shrunk == body:
                break
            body = shrunk
        return body

    def fast_command(self, command: str) -> Optional[str]:
        """Answer with one tool call, or ``None`` to let the agent think.

        Returns the message to post when it is done, and ``None`` when this is not
        one of the few things worth answering without a model - a wrong guess here
        costs nothing but the seconds the agent was going to spend anyway.
        """
        body = self._bare(command)
        if not body:
            return None
        # A URL first: "open youtube.com" also reads as opening something called
        # youtube.com, and that branch would decide it is not an app and end the
        # whole shortcut instead of handing it to this one.
        if match := _FAST_URL.fullmatch(body):
            return _plain(self._tools.open_url(match.group("url")))
        if match := _FAST_OPEN.fullmatch(body):
            return self._open(match.group("what").strip().lower())
        if match := _FAST_CLOSE.fullmatch(body):
            return self._close(match.group("what").strip())
        return None

    def _open(self, name: str) -> Optional[str]:
        """Launch an app, and only claim it when a window can be seen for it."""
        if name not in self._tools.known_apps():
            return None                   # not a name anyone vouched for: think
        try:
            before = set(self._tools.list_windows())
        except Exception as exc:
            log.warn(f"Discord: cannot see the window list ({exc}); using the agent.")
            return None
        try:
            said = self._tools.open_app(name)
        except Exception as exc:
            log.warn(f"Discord: could not launch '{name}' ({exc}); using the agent.")
            return None
        hint = name.split()[0]
        deadline = time.monotonic() + self._wait
        while True:
            try:
                titles = self._tools.list_windows()
            except Exception:
                return None
            window = next((t for t in titles
                           if name in t.lower() or hint in t.lower()), "")
            if window:
                return f"{said.capitalize() if said else 'Opened'} - '{window}' is up."
            if time.monotonic() >= deadline:
                # Nothing to show for it. Windows' own error dialog is the usual
                # reason, and the agent can read a screen where this cannot.
                log.warn(f"Discord: launched '{name}' but no window appeared; "
                         "handing it to the agent.")
                return None
            time.sleep(0.15)

    def _close(self, what: str) -> Optional[str]:
        """Close a window. ``close_window`` matches a title or says it did not."""
        try:
            said = _plain(self._tools.close_window(what))
        except Exception as exc:
            log.warn(f"Discord: could not close '{what}' ({exc}); using the agent.")
            return None
        return said if said.lower().startswith("closed") else None

    def __call__(self, command: str) -> str:
        with self._lock() as won:
            if not won:
                self.refused += 1
                log.warn("Discord: the desktop is in use; refused to start another "
                         "task on top of it.")
                return ("I am already doing something on that computer, so I did "
                        "not start this as well. Say it again when I am done.")
            started = time.perf_counter()
            try:
                fast = self.fast_command(command)
            except Exception as exc:      # never let the shortcut break the task
                log.warn(f"Discord: the fast path failed ({exc}); using the agent.")
                fast = None
            if fast is not None:
                took = time.perf_counter() - started
                self.fast += 1
                self.fast_seconds += took
                log.ok(f"Discord: answered without the model in {took:.1f}s.")
                return fast
            result = self.agent.run(command, asker=self.asker)
        return str(result or "")


def main(argv: Optional[list[str]] = None) -> int:
    """Run the listener in the foreground until interrupted.

    Its own process, for the reason in the module docstring. ``--check`` answers
    "is this configured?" and exits - the question worth asking before leaving
    something running that answers whoever is on the allowlist.

    Configuration is asked about *before* a model client is built: "listening is
    switched off" is the answer for most people running this, and it costs a
    provider handshake to learn otherwise.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    from .config import load_config

    cfg = load_config()
    bot = DiscordBot(cfg=cfg)
    if why := bot.why_not(need_brain=False):
        log.warn(f"Discord: {why}")
        return 1
    try:
        from .agent import brain as brain_module

        bot.brain = brain_module.make_brain(cfg.brain)
    except Exception as exc:
        log.warn(f"Discord: no model to answer with ({exc}).")
        log.info("  run 'python run.py --check' to see why the brain is unusable.")
        return 1
    if _truthy(AGENT_ENV):
        try:
            from .agent.loop import Agent

            bot.runner = DiscordRunner(Agent(bot.brain, cfg), asker=bot.ask_question)
            log.info(f"Discord: {AGENT_ENV} is on - a message that reads like a "
                     "command will be carried out on this computer.")
        except Exception as exc:
            log.warn(f"Discord: {AGENT_ENV} is on but the agent will not build "
                     f"({exc}); answering in words only.")
    if "--check" in args:
        acting = ("command that reads like one is carried out on this computer"
                  if bot._task_mode() else
                  f"talking only - set {AGENT_ENV}=1 to let it act on the desktop")
        log.ok(f"Discord: ready to listen - intents {INTENTS} (message content "
               f"included), answering {', '.join(sorted(bot.allowed()))}")
        log.info(f"  a {acting}")
        log.info("  start it with: python -m jarvis.discord_bot")
        return 0
    if not bot.start():
        log.warn(f"Discord: could not start listening - {bot.last_error}")
        return 1
    log.ok("Discord: listening. Ctrl-C to stop.")
    try:
        while bot.running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        log.info("Discord: stopping.")
    finally:
        bot.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
