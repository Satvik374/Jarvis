"""Behavioural tests for messaging Jarvis on Discord.

The words of a reply come from the model, so a fake brain stands in for the
provider: what these tests pin is the policy wrapped around it - who is answered
and who is refused, what a server message has to do to be answered at all, where
the answer is posted, how a long answer is split, and what happens when Discord
will not say who the owner is.

No socket is opened and no message is sent: the gateway, the sender and the owner
lookup are all injected, so what is under test is this module's own decisions.
The last section covers the write that carries the answer out
(``connectors.discord_send``), including the read cache it must not go through.
"""

from __future__ import annotations

import pathlib
import re
from unittest.mock import Mock

import pytest
import requests

from jarvis import discord_bot
from jarvis.tools import connectors

OWNER = "1222437883114426418"


def _plain(output: str) -> str:
    """Log output without its ANSI colouring, the way the other gates read it."""
    return " ".join(re.sub(r"\x1b\[[0-9;]*m", "", output).split())


class FakeBrain:
    """A provider stand-in that records its prompts and replays given replies."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts: list[tuple[str, list[dict]]] = []

    def complete(self, system, messages, image=None):
        self.prompts.append((system, messages))
        return self.replies.pop(0) if self.replies else "Understood."


class FlakyBrain:
    """A provider that fails a given number of times before it works."""

    def __init__(self, failures: int = 1, reply: str = "Second try."):
        self.failures = failures
        self.reply = reply
        self.calls = 0

    def complete(self, system, messages, image=None):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("the AI service is busy")
        return self.reply


class Recorder:
    """Stands in for ``connectors.discord_send`` and keeps what it was given."""

    def __init__(self):
        self.sent: list[tuple[str, str, str]] = []

    def __call__(self, channel_id, body, reply_to=""):
        self.sent.append((channel_id, body, reply_to))
        return f"mid{len(self.sent)}"


def _message(**over) -> dict:
    """A direct message from the owner, in Discord's own shape."""
    message = {
        "id": "1001",                  # Discord's ids are digits, and the
        "channel_id": "2002",          # reply-reference handling relies on it

        "guild_id": None,
        "content": "hello",
        "author": {"id": OWNER, "username": "moralta_gaming", "bot": False},
    }
    message.update(over)
    return message


def _enable(monkeypatch) -> None:
    """Turn listening on, the way a real setup does, so ``start`` is allowed."""
    monkeypatch.setenv(discord_bot.ENABLE_ENV, "1")


def _bot(brain=None, send=None, allow=(OWNER,), **kwargs) -> discord_bot.DiscordBot:
    return discord_bot.DiscordBot(brain=brain or FakeBrain("Hi."),
                                  allow=list(allow), send=send or Recorder(),
                                  **kwargs)


def test_a_direct_message_is_answered_and_the_answer_replies_to_it(monkeypatch):
    """The whole point: a DM is answered, in the channel it arrived in."""
    monkeypatch.delenv("JARVIS_DISCORD_GUILD", raising=False)
    send = Recorder()
    bot = _bot(brain=FakeBrain("Morning - all quiet here."), send=send)

    reply = bot.handle_message(_message())

    assert reply == "Morning - all quiet here."
    assert send.sent == [("2002", "Morning - all quiet here.", "1001")]
    assert bot.answered == 1


def test_a_stranger_is_never_answered(monkeypatch):
    """Anyone who can DM a public bot is not therefore allowed to use it."""
    monkeypatch.delenv("JARVIS_DISCORD_GUILD", raising=False)
    send = Recorder()
    bot = _bot(send=send)

    reply = bot.handle_message(_message(
        author={"id": "9999", "username": "random_dm", "bot": False}))

    assert reply == ""
    assert send.sent == []
    assert bot.answered == 0


def test_a_bot_is_never_answered_even_when_it_is_allowed(monkeypatch):
    """Two bots answering each other is an infinite loop, not a conversation."""
    monkeypatch.delenv("JARVIS_DISCORD_GUILD", raising=False)
    send = Recorder()
    bot = _bot(send=send, allow=(OWNER, "other-bot"))

    bot.handle_message(_message(author={"id": "other-bot", "bot": True}))

    assert send.sent == []


def test_its_own_message_is_ignored(monkeypatch):
    """Discord echoes the bot's own messages back; answering them never ends."""
    monkeypatch.delenv("JARVIS_DISCORD_GUILD", raising=False)
    send = Recorder()
    bot = _bot(send=send, allow=("selfbot",))
    bot.bot_id = "selfbot"

    bot.handle_message(_message(author={"id": "selfbot", "bot": False}))

    assert send.sent == []


def test_a_server_message_is_ignored_until_the_bot_is_mentioned(monkeypatch):
    """A bot in a busy server must not comment on every message it can see."""
    monkeypatch.delenv("JARVIS_DISCORD_GUILD", raising=False)
    send = Recorder()
    brain = FakeBrain("Sure.")
    bot = _bot(brain=brain, send=send)
    bot.bot_id = "123"

    assert bot.handle_message(_message(guild_id="g1", content="hi everyone")) == ""
    assert send.sent == []

    reply = bot.handle_message(_message(id="1002", guild_id="g1",
                                       content="<@123> what time is it?"))

    assert reply == "Sure."
    # The mention is addressing, not content: the model never sees it.
    assert brain.prompts[0][1][-1]["content"] == "what time is it?"


def test_channel_answers_can_be_switched_on(monkeypatch):
    monkeypatch.setenv("JARVIS_DISCORD_GUILD", "1")
    send = Recorder()
    bot = _bot(send=send)
    bot.bot_id = "123"

    assert bot.handle_message(_message(guild_id="g1", content="status?")) == "Hi."
    assert len(send.sent) == 1


def test_the_same_message_is_answered_once(monkeypatch):
    """Discord redelivers events; a redelivered instruction is not a second one."""
    monkeypatch.delenv("JARVIS_DISCORD_GUILD", raising=False)
    send = Recorder()
    bot = _bot(send=send)

    first = bot.handle_message(_message())
    again = bot.handle_message(_message())

    assert first == "Hi." and again == ""
    assert len(send.sent) == 1


def test_the_allowlist_defaults_to_the_application_owner(monkeypatch):
    """With no list configured, the account that made the bot may use it."""
    monkeypatch.delenv("DISCORD_ALLOWED_USERS", raising=False)
    monkeypatch.delenv("JARVIS_DISCORD_GUILD", raising=False)
    send = Recorder()
    bot = discord_bot.DiscordBot(brain=FakeBrain("Yes?"), send=send,
                                 owner_lookup=lambda: OWNER)

    assert bot.allowed() == frozenset({OWNER})
    assert bot.handle_message(_message()) == "Yes?"
    assert bot.handle_message(_message(id="1002", author={
        "id": "1", "username": "someone_else", "bot": False})) == ""
    assert len(send.sent) == 1


def test_a_username_in_the_allowlist_works(monkeypatch):
    """Nobody knows their own id by heart, so the name they see is accepted."""
    monkeypatch.delenv("JARVIS_DISCORD_GUILD", raising=False)
    send = Recorder()
    bot = _bot(send=send, allow=("@moralta_gaming",))

    assert bot.handle_message(_message()) == "Hi."
    assert len(send.sent) == 1


def test_an_owner_discord_will_not_name_allows_nobody(monkeypatch):
    """Failing closed, and saying why, is the only safe reading of an unknown
    owner: a bot that cannot name who may drive it must not answer anyone."""
    monkeypatch.delenv("DISCORD_ALLOWED_USERS", raising=False)
    _enable(monkeypatch)
    send = Recorder()

    def refuse():
        raise RuntimeError("429 rate limited")

    bot = discord_bot.DiscordBot(brain=FakeBrain("Hi."), send=send,
                                 token="bot-token", owner_lookup=refuse)

    assert bot.allowed() == frozenset()
    assert bot.handle_message(_message()) == ""
    assert send.sent == []
    why = bot.why_not()
    assert "DISCORD_ALLOWED_USERS" in why and "429 rate limited" in why
    assert bot.start() is False
    assert bot.running is False


def test_a_failed_owner_lookup_is_retried_rather_than_remembered(monkeypatch):
    """One bad moment on the network must not switch the listener off for the
    rest of the session."""
    monkeypatch.delenv("DISCORD_ALLOWED_USERS", raising=False)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("temporary DNS failure")
        return OWNER

    bot = discord_bot.DiscordBot(brain=FakeBrain("Hi."), send=Recorder(),
                                 owner_lookup=flaky)

    assert bot.allowed() == frozenset()
    assert bot.allowed() == frozenset({OWNER})


def test_a_long_reply_is_split_into_messages_discord_accepts(monkeypatch):
    """Discord refuses a body over 2000 characters outright, so a long answer
    arrives as consecutive messages instead of not arriving at all."""
    monkeypatch.delenv("JARVIS_DISCORD_GUILD", raising=False)
    send = Recorder()
    bot = _bot(brain=FakeBrain("x" * 4500), send=send)

    bot.handle_message(_message())

    assert [len(body) for _, body, _ in send.sent] == [2000, 2000, 500]
    # Only the first message is a reply, or the phone draws three threads.
    assert [reply_to for _, _, reply_to in send.sent] == ["1001", "", ""]
    assert "".join(body for _, body, _ in send.sent) == "x" * 4500


def test_an_agent_action_envelope_is_unwrapped(monkeypatch):
    """The configured model also speaks the agent loop's JSON envelope; posting
    it raw would put braces on the user's phone."""
    monkeypatch.delenv("JARVIS_DISCORD_GUILD", raising=False)
    send = Recorder()
    bot = _bot(send=send, brain=FakeBrain(
        '{"action": "finish", "message": "Done - it is 4pm."}'))

    assert bot.handle_message(_message()) == "Done - it is 4pm."
    assert send.sent[0][1] == "Done - it is 4pm."


def test_the_agent_runner_is_used_only_when_the_environment_asks(monkeypatch):
    """Handing Discord the desktop is opt-in: the console always passes a runner,
    and this switch is what decides whether it is ever called."""
    monkeypatch.delenv("JARVIS_DISCORD_GUILD", raising=False)
    monkeypatch.delenv("JARVIS_DISCORD_AGENT", raising=False)
    ran: list[str] = []
    send = Recorder()
    brain = FakeBrain("I can only talk from here.")
    bot = _bot(brain=brain, send=send,
               runner=lambda command: ran.append(command) or "ran it")

    assert bot.handle_message(_message(content="open Spotify")) == \
        "I can only talk from here."
    assert ran == []

    monkeypatch.setenv("JARVIS_DISCORD_AGENT", "1")
    assert bot.handle_message(_message(id="1002", content="open Spotify")) == "ran it"
    assert ran == ["open Spotify"]


def test_a_message_without_text_is_ignored(monkeypatch):
    """This is what a missing Message Content intent looks like from here: a
    message that arrives with nothing in it."""
    monkeypatch.delenv("JARVIS_DISCORD_GUILD", raising=False)
    send = Recorder()
    bot = _bot(send=send)

    assert bot.handle_message(_message(content="")) == ""
    assert send.sent == []


def test_the_conversation_is_remembered_per_channel(monkeypatch):
    """A second question in the same channel continues the first; another channel
    starts clean, so two conversations do not bleed into each other."""
    monkeypatch.delenv("JARVIS_DISCORD_GUILD", raising=False)
    brain = FakeBrain("One.", "Two.", "Three.")
    bot = _bot(brain=brain, send=Recorder())

    bot.handle_message(_message(content="first there"))
    bot.handle_message(_message(id="1002", content="and now?"))
    assert [turn["content"] for turn in brain.prompts[1][1]] == [
        "first there", "One.", "and now?"]

    bot.handle_message(_message(id="1003", channel_id="2003", content="hello"))
    assert [turn["content"] for turn in brain.prompts[2][1]] == ["hello"]


def test_a_completion_that_is_not_text_is_never_posted(monkeypatch):
    """The reported bug, pinned. A stand-in brain replies with a ``Mock``, and the
    old coercion posted its ``repr`` - a literal ``<Mock name='agent.brain.
    complete()' id=...>`` - to a real Discord message."""
    monkeypatch.delenv("JARVIS_DISCORD_GUILD", raising=False)
    send = Recorder()
    # The exact shape that produced it: the console's agent stub, whose .brain
    # returns a Mock from complete().
    agent_stub = Mock(name="agent")
    bot = discord_bot.DiscordBot(brain=agent_stub.brain, allow=[OWNER], send=send)

    reply = bot.handle_message(_message(content="Hi"))

    assert "Mock" not in reply and "id=" not in reply
    assert len(send.sent) == 1
    body = send.sent[0][1]
    assert "Mock" not in body
    assert body == discord_bot._NO_ANSWER
    assert "nothing that can be sent" in bot.last_error


def test_only_a_string_counts_as_prose():
    """Unit-level: the coercion that let any object through is gone."""
    for value in (Mock(name="agent"), None, 123, [{"text": "hi"}], {"a": 1}, b"hi"):
        assert discord_bot._plain(value) == "", f"{value!r} was treated as prose"
    assert discord_bot._plain("  hello  ") == "hello"
    assert discord_bot._plain('"quoted"') == "quoted"
    # A JSON envelope is still unwrapped, because the model speaks it.
    assert discord_bot._plain('{"message": "done"}') == "done"


def test_a_reply_needs_a_brain_that_is_still_standing(monkeypatch, caplog):
    """A provider that fails one message must not take the listener down, and the
    next message must still be answered."""
    monkeypatch.delenv("JARVIS_DISCORD_GUILD", raising=False)
    send = Recorder()
    brain = FlakyBrain()
    bot = _bot(brain=brain, send=send)

    assert bot.handle_message(_message()) == ""
    assert send.sent == []
    assert "busy" in bot.last_error

    assert bot.handle_message(_message(id="1002")) == "Second try."
    assert len(send.sent) == 1


def test_no_token_means_it_never_opens_a_connection(monkeypatch):
    """Nothing is started without a token, and the reason names the fix."""
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    _enable(monkeypatch)
    bot = discord_bot.DiscordBot(brain=FakeBrain("Hi."), allow=[OWNER])

    assert "DISCORD_BOT_TOKEN" in bot.why_not()
    assert bot.start() is False
    assert bot.running is False


def test_the_privileged_message_content_intent_is_asked_for():
    """Without this intent a message arrives with empty text: the listener looks
    connected and answers nobody. It is a privileged intent, so requesting it is
    a decision worth pinning."""
    assert discord_bot.INTENTS & (1 << 15)
    # Direct messages and guild messages, or there is nothing to answer.
    assert discord_bot.INTENTS & (1 << 12)
    assert discord_bot.INTENTS & (1 << 9)


def test_the_close_codes_that_cannot_be_retried_are_explained():
    """A refusal with no explanation is the worst version of this feature: the
    listener would reconnect forever over a token that will never work."""
    assert 4004 in discord_bot._FATAL_CLOSE
    assert 4014 in discord_bot._FATAL_CLOSE
    assert "Message Content" in discord_bot._FATAL_CLOSE[4014]


def test_status_says_why_it_is_off_rather_than_claiming_to_listen(monkeypatch):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    _enable(monkeypatch)
    bot = discord_bot.DiscordBot(brain=FakeBrain("Hi."), allow=[OWNER])

    assert "off" in bot.status() and "DISCORD_BOT_TOKEN" in bot.status()


def test_listening_is_off_until_it_is_asked_for(monkeypatch):
    """A working token is what Jarvis *can* do, not what it starts doing on
    every launch - and it is what keeps a test that drives the console from
    opening a websocket to Discord."""
    monkeypatch.delenv(discord_bot.ENABLE_ENV, raising=False)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "bot-token")
    bot = discord_bot.DiscordBot(brain=FakeBrain("Hi."), allow=[OWNER])

    assert bot.start() is False
    assert bot.running is False
    # The reason names the switch, and the credential it still needs.
    assert discord_bot.ENABLE_ENV in bot.why_not()
    assert "DISCORD_BOT_TOKEN" in bot.why_not()
    assert "off" in bot.status()


def test_check_answers_configuration_before_building_a_model(monkeypatch, capsys):
    """`--check` for most people answers "listening is switched off", and it must
    say that without paying for a provider handshake first."""
    # Blanked, not deleted: `main` calls load_config, whose load_dotenv refills a
    # name that is merely absent - and this machine's .env turns listening on, so
    # deleting it here would test nothing. Empty is falsy, so the override skips.
    monkeypatch.setenv(discord_bot.ENABLE_ENV, "")
    monkeypatch.setattr("jarvis.agent.brain.make_brain",
                        lambda *a, **k: pytest.fail("built a model client to be "
                                                    "told listening is off"))

    assert discord_bot.main(["--check"]) == 1
    assert discord_bot.ENABLE_ENV in _plain(capsys.readouterr().out)


def test_check_reports_ready_without_opening_a_connection(monkeypatch, capsys):
    """The point of `--check` is to answer the question without doing the thing.

    Hermetic on purpose: the token is set here rather than read out of ``.env``,
    which is gitignored - a test that needs the developer's own ``.env`` passes on
    one machine and fails on a clone, which is exactly what it did until the
    staged tree was run without one.
    """
    _enable(monkeypatch)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "bot-token")
    monkeypatch.setenv("DISCORD_ALLOWED_USERS", OWNER)
    monkeypatch.setattr("jarvis.agent.brain.make_brain", lambda *a, **k: object())
    monkeypatch.setattr(discord_bot.DiscordBot, "start",
                        lambda self, *a, **k: pytest.fail("`--check` connected"))

    monkeypatch.setenv(discord_bot.AGENT_ENV, "1")
    assert discord_bot.main(["--check"]) == 0
    out = _plain(capsys.readouterr().out)
    assert OWNER in out and "message content" in out
    assert "carried out on this computer" in out, \
        "--check should say that a command will act, not leave it to be found out"

    # And with acting off it says that instead, rather than saying nothing.
    monkeypatch.setenv(discord_bot.AGENT_ENV, "")
    assert discord_bot.main(["--check"]) == 0
    assert "talking only" in _plain(capsys.readouterr().out)


def test_the_console_never_starts_the_listener_in_process():
    """A source guard, and the reason is measured rather than aesthetic.

    Holding a gateway connection inside the console wedges the floating HUD's
    teardown: `:quit` reaches ``mini_overlay.stop()`` -> ``tkinter`` destroy, which
    waits on a Tcl interpreter whose thread has already gone and never returns. A
    plain thread at the same point in startup is harmless, and so is an asyncio
    loop, so this is specifically the connection - which is why the listener runs
    as its own process instead, and why nothing in the console should grow a call
    to it back.
    """
    console_source = (pathlib.Path(__file__).resolve().parent.parent
                      / "jarvis" / "console.py").read_text(encoding="utf-8")

    assert "discord_bot" not in console_source


def test_an_enabled_listener_still_refuses_without_an_allowlist(monkeypatch):
    """The enable switch is not a bypass: with nobody named it stays shut."""
    _enable(monkeypatch)
    monkeypatch.delenv("DISCORD_ALLOWED_USERS", raising=False)

    def refuse():
        raise RuntimeError("no route to Discord")

    bot = discord_bot.DiscordBot(brain=FakeBrain("Hi."), token="bot-token",
                                 owner_lookup=refuse)

    assert bot.start() is False
    assert bot.running is False


# --------------------------------------------------------------------------- #
# The write that carries the answer out
# --------------------------------------------------------------------------- #

def _sent_by(monkeypatch) -> list[dict]:
    """Capture POSTs to Discord without a network, and pretend a token is set."""
    posts: list[dict] = []

    class Response:
        status_code = 200
        text = ""
        ok = True

        @staticmethod
        def json():
            return {"id": "123456789"}

    def fake_post(url, **kwargs):
        posts.append({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(connectors, "_env",
                        lambda *names: tuple("bot-token" for _ in names))
    connectors.invalidate()
    return posts


def test_the_send_op_posts_the_message_to_that_channel(monkeypatch):
    posts = _sent_by(monkeypatch)
    monkeypatch.setattr(connectors, "_resolve_channel",
                        lambda target: {"id": "555", "name": target})

    out = connectors.fetch("discord", "send", "hello from Jarvis", "#general")

    assert len(posts) == 1
    assert posts[0]["url"].endswith("/channels/555/messages")
    assert posts[0]["json"]["content"] == "hello from Jarvis"
    assert posts[0]["headers"]["Authorization"] == "Bot bot-token"
    assert "555" in out


def test_a_reply_is_marked_as_a_reply_and_survives_a_deleted_original(monkeypatch):
    posts = _sent_by(monkeypatch)
    monkeypatch.setattr(connectors, "_resolve_channel",
                        lambda target: {"id": "555", "name": target})

    connectors.discord_send("555", "answer", reply_to="1001")

    reference = posts[0]["json"]["message_reference"]
    assert reference["message_id"] == "1001"
    # Without this, a message deleted in the meantime loses the answer itself.
    assert reference["fail_if_not_exists"] is False


def test_a_send_is_never_answered_from_the_read_cache(monkeypatch):
    """Two identical sends are two messages the user asked for. Served from the
    memo, the second would be reported as sent and never leave the machine."""
    posts = _sent_by(monkeypatch)

    connectors.fetch("discord", "send", "same text", "555")
    connectors.fetch("discord", "send", "same text", "555")

    assert len(posts) == 2


def test_a_send_with_no_text_is_refused_before_any_request(monkeypatch):
    posts = _sent_by(monkeypatch)

    try:
        connectors.fetch("discord", "send", "   ", "555")
    except connectors.ConnectorError as exc:
        assert "query" in str(exc)
    else:
        raise AssertionError("an empty send must be refused")

    assert posts == []


# --------------------------------------------------------------------------- #
# acting on the desktop, not just talking about it
# --------------------------------------------------------------------------- #

class FakeAgent:
    """Stands in for the agent loop: the router, and a run that can ask a question."""

    def __init__(self, act=True, asks=None, result="Done.", router=None):
        self.act = act
        self.asks = asks
        self.result = result
        self.router = router
        self.ran: list[str] = []

    def _looks_like_task(self, text: str) -> bool:
        if self.router is not None:
            return self.router(text)
        return self.act and text.strip().lower().startswith(("open", "close"))

    def run(self, task, asker=None):
        self.ran.append(task)
        if self.asks is None:
            return self.result
        answer = asker(self.asks)
        return self.result if not answer else f"using {answer}"


def _runner(agent, asker=None, lock=None):
    """A runner whose tool layer answers nothing, so the agent path is what runs.

    The real tool layer is the production default, and a test that used it would
    launch real applications on the machine running the suite - so every runner
    here gets a fake, and ``known=set()`` keeps the fast path out of the way of
    the tests that are about the agent.
    """
    return discord_bot.DiscordRunner(agent, asker=asker, lock=lock,
                                     tools=FakeTools(known=set()))


def _wait_until(predicate, timeout=5.0) -> bool:
    """Wait for something another thread does, without sleeping a fixed guess."""
    import time as _time

    deadline = _time.time() + timeout
    while _time.time() < deadline:
        if predicate():
            return True
        _time.sleep(0.01)
    return False


@pytest.mark.parametrize("markup", [
    # Both were seen from a real model on one evening, the second being what a
    # person got instead of an opened calculator.
    '<minimax:tool_call> <invoke name="system_status"> </invoke> </minimax:tool_call>',
    'Opening calculator now. [TOOL_CALL]{tool => "desktop-commander_start-app", '
    'args => { --appName "calculator" }}',
])
def test_raw_tool_call_markup_never_reaches_a_phone(monkeypatch, markup):
    """Asked in *conversation*, the model reaches for a tool it has no hand on and
    writes the call as markup in whatever dialect it learned. That is not prose,
    and posting it puts call syntax in a message - so with nothing able to act on
    it, it reads as a failure and the reason goes to the log instead."""
    monkeypatch.delenv("JARVIS_DISCORD_GUILD", raising=False)
    monkeypatch.delenv(discord_bot.AGENT_ENV, raising=False)
    send = Recorder()
    bot = _bot(send=send, brain=FakeBrain(markup))

    assert bot.handle_message(_message()) == discord_bot._NO_ANSWER
    assert not discord_bot._TOOL_MARKUP.search(send.sent[0][1])
    assert bot.last_error


def test_a_chat_reply_that_reaches_for_a_tool_is_carried_out(monkeypatch):
    """The model saying "this is an action" is better evidence than any verb list:
    asked in conversation to open the calculator it answered with a tool call, and
    the person got the call syntax instead of a calculator. With an agent to hand,
    that answer is an instruction - so go and do it."""
    monkeypatch.delenv("JARVIS_DISCORD_GUILD", raising=False)
    monkeypatch.setenv(discord_bot.AGENT_ENV, "1")
    send = Recorder()
    agent = FakeAgent(act=False, result="Calculator is open.")
    bot = _bot(send=send, brain=FakeBrain(
        'Opening calculator now. [TOOL_CALL]{tool => "desktop-commander_start-app", '
        'args => { --appName "calculator" }}'), runner=_runner(agent))

    assert bot.handle_message(_message(content="can you launch the calculator")) == \
        "Calculator is open."
    assert agent.ran == ["can you launch the calculator"]
    assert not any(discord_bot._TOOL_MARKUP.search(body)
                   for _, body, _ in send.sent)


class FakeTools:
    """Stands in for the tool layer, so no app is launched by a test."""

    def __init__(self, apps=("calculator", "notepad"), windows=None,
                 known=None):
        self.apps = set(apps)
        self.windows = list(windows if windows is not None
                            else ["Calculator", "Discord"])
        # ``is not None``, not ``or``: an empty set is exactly what a test passes
        # to keep the fast path out of the way, and it is falsy.
        self.known = set(known if known is not None else self.apps)
        self.launched: list[str] = []
        self.closed: list[str] = []
        self.urls: list[str] = []

    def known_apps(self):
        return set(self.known)

    def open_app(self, name):
        self.launched.append(name)
        return f"launched '{name}'"        # says this for names Windows cannot find

    def close_window(self, title):
        self.closed.append(title)
        hit = next((w for w in self.windows if title.lower() in w.lower()), "")
        return f"closed '{hit}'" if hit else f"no window matching '{title}'"

    def open_url(self, url):
        self.urls.append(url)
        return f"opened {url}"

    def list_windows(self):
        return list(self.windows)


def _fast_runner(agent, tools, wait=0.2):
    return discord_bot.DiscordRunner(agent, tools=tools, wait=wait)


def test_a_simple_command_is_answered_without_the_model(monkeypatch):
    """A completion costs ~10.5s on the configured provider and a ten-call task
    was 137s, while opening an app is one tool call taking a second. The messages
    people send from a phone are mostly this shape, so they must not wait for a
    model - and the claim is that the agent was never asked at all."""
    monkeypatch.delenv("JARVIS_DISCORD_GUILD", raising=False)
    monkeypatch.setenv(discord_bot.AGENT_ENV, "1")
    send = Recorder()
    agent, tools = FakeAgent(router=_real_router()), FakeTools()
    runner = _fast_runner(agent, tools)
    bot = _bot(send=send, runner=runner)

    reply = bot.handle_message(_message(content="Can you open calculator for me"))

    assert tools.launched == ["calculator"]
    assert "Calculator" in reply
    assert agent.ran == [], "the agent is what costs the minutes"
    assert runner.fast == 1
    assert send.sent[-1][1] == reply


def test_the_fast_path_drives_the_real_tool_layer(monkeypatch):
    """The production default has to be the real functions the agent's handlers
    call - a stub left in by accident would answer "opened" without opening
    anything. Monkeypatched so this guard cannot launch a window either."""
    from jarvis.tools import apps

    called: list[str] = []
    monkeypatch.setattr(apps, "_KNOWN", {"calculator": "calc.exe"})
    monkeypatch.setattr(apps, "list_windows", lambda: ["Calculator"])
    monkeypatch.setattr(apps, "open_app",
                        lambda name: called.append(name) or f"launched '{name}'")

    runner = discord_bot.DiscordRunner(FakeAgent())      # no tools= : the default

    assert "Calculator" in runner.fast_command("open calculator")
    assert called == ["calculator"]


def test_a_url_is_opened_without_the_model(monkeypatch):
    """"open youtube.com" also reads as opening something called youtube.com, so
    the URL has to be recognised as one."""
    monkeypatch.setenv(discord_bot.AGENT_ENV, "1")
    tools = FakeTools()
    runner = _fast_runner(FakeAgent(), tools)

    assert runner.fast_command("open youtube.com") == "opened youtube.com"
    assert runner.fast_command("open https://github.com/x/y") == \
        "opened https://github.com/x/y"
    assert tools.urls == ["youtube.com", "https://github.com/x/y"]
    assert tools.launched == []


def test_a_command_one_tool_call_cannot_answer_goes_to_the_agent(monkeypatch):
    """A file, a plan, or a name nobody vouched for is not this shortcut's
    business: the agent can read a screen where a launch-and-hope cannot."""
    monkeypatch.setenv(discord_bot.AGENT_ENV, "1")
    tools = FakeTools()
    agent = FakeAgent(result="Done.")
    runner = _fast_runner(agent, tools)

    for phrase in ("open the report.pdf", "open calculator and open notepad",
                   "open bogus-app-xyz"):
        assert runner.fast_command(phrase) is None, phrase
    assert tools.launched == [], "an unvouched name is never launched"
    assert tools.urls == []


def test_a_launch_that_leaves_no_window_is_handed_to_the_agent(monkeypatch):
    """``open_app`` answers "launched 'bogus-app-xyz'" for a name Windows cannot
    find, with an error dialog on the screen - so the window list is the evidence,
    and no window means the claim is not made."""
    monkeypatch.setenv(discord_bot.AGENT_ENV, "1")
    tools = FakeTools(windows=["Discord"])       # the app never appears
    agent = FakeAgent(result="I could not find that app.")
    runner = _fast_runner(agent, tools)

    assert runner.fast_command("open calculator") is None
    assert tools.launched == ["calculator"], "it was tried once, then handed over"

    bot = _bot(send=Recorder(), runner=runner)
    assert bot.handle_message(_message(content="open calculator")) == \
        "I could not find that app."
    assert agent.ran == ["open calculator"]
    assert runner.fast == 0


def test_closing_a_window_the_tool_cannot_find_goes_to_the_agent(monkeypatch):
    monkeypatch.setenv(discord_bot.AGENT_ENV, "1")
    tools = FakeTools()
    runner = _fast_runner(FakeAgent(), tools)

    assert runner.fast_command("close calculator") == "closed 'Calculator'"
    assert runner.fast_command("close the spreadsheet") is None
    # "the" is a determiner, not part of a title: the tool matches on substring.
    assert tools.closed == ["calculator", "spreadsheet"]


def test_the_fast_path_never_breaks_the_task(monkeypatch):
    """A shortcut that throws is a shortcut that should not have existed: the
    command still runs, by the agent, and the failure is logged."""
    monkeypatch.setenv(discord_bot.AGENT_ENV, "1")

    class Exploding(FakeTools):
        def known_apps(self):
            raise RuntimeError("no window list today")

    agent = FakeAgent(result="Done it the slow way.")
    runner = _fast_runner(agent, Exploding())
    bot = _bot(send=Recorder(), runner=runner)

    assert bot.handle_message(_message(content="open calculator")) == \
        "Done it the slow way."
    assert agent.ran == ["open calculator"]


def _real_router():
    """The console's own router, without building a brain or a desktop for it.

    The phrases under test are the *real* ones - "can you open calculator for me"
    is what a person actually sent - so the router under test has to be the real
    one too. A stub would only ever confirm the stub.
    """
    from jarvis.agent.loop import Agent

    agent = object.__new__(Agent)      # the router is pure: no brain, no desktop
    for name in dir(Agent):
        if name.startswith(("_TASK_", "_UI_", "_KEY_")):
            setattr(agent, name, getattr(Agent, name))
    return agent._looks_like_task


def test_a_polite_command_is_offered_to_the_router_without_its_tail(monkeypatch):
    """"Can you open calculator for me" is a command, and the real router says no
    to it: it understands wrappers at the *front* ("can you") and nothing after
    the object ("for me"). Reading it as conversation is what left a phone
    message answered by markup, so the trailing politeness comes off before the
    question is put a second time."""
    monkeypatch.delenv("JARVIS_DISCORD_GUILD", raising=False)
    monkeypatch.setenv(discord_bot.AGENT_ENV, "1")
    real, asked = _real_router(), []

    def router(text):
        asked.append(text)
        return real(text)

    agent = FakeAgent(router=router, result="Calculator is open.")
    bot = _bot(send=Recorder(), runner=_runner(agent))

    assert bot.handle_message(_message(content="Can you open calculator for me")) == \
        "Calculator is open."
    assert asked == ["Can you open calculator for me", "Can you open calculator"], \
        "the phrase is tried as written first, then without its tail"
    assert agent.ran == ["Can you open calculator for me"], \
        "the router sees a trimmed copy; the task keeps the words they used"


def test_politeness_alone_never_turns_talk_into_a_command(monkeypatch):
    """The second try is only a second try: the real router still has to recognise
    a command in what is left, so a sentence that merely ends politely is still a
    conversation, and nothing moves the mouse."""
    monkeypatch.delenv("JARVIS_DISCORD_GUILD", raising=False)
    monkeypatch.setenv(discord_bot.AGENT_ENV, "1")
    real = _real_router()
    agent = FakeAgent(router=real)
    bot = _bot(brain=FakeBrain(*["Glad to help."] * 3), send=Recorder(),
               runner=_runner(agent))

    for phrase in ("that is great for me", "how much disk space do I have",
                   "thanks for me"):
        assert bot.handle_message(_message(id=f"9{len(phrase)}", content=phrase)) \
            == "Glad to help."
    assert agent.ran == []


def test_a_command_is_carried_out_on_the_desktop_and_talk_is_not(monkeypatch):
    """The feature: with the flag on, a message that reads like a command moves
    this computer, and one that does not is still only a conversation."""
    monkeypatch.delenv("JARVIS_DISCORD_GUILD", raising=False)
    monkeypatch.setenv(discord_bot.AGENT_ENV, "1")
    send, agent = Recorder(), FakeAgent()
    bot = _bot(brain=FakeBrain("I am well."), send=send,
               runner=_runner(agent, asker=None))

    assert bot.handle_message(_message(content="open the calculator")) == "Done."
    assert agent.ran == ["open the calculator"]
    # Acknowledged first, so the person who walked away sees it land.
    assert send.sent[0][1].startswith("🛠")
    assert send.sent[1][1] == "Done."

    assert bot.handle_message(_message(id="1009", content="how are you")) == \
        "I am well."
    assert agent.ran == ["open the calculator"]


def test_a_router_that_fails_makes_jarvis_talk_rather_than_act(monkeypatch):
    """When it cannot tell a command from a pleasantry the harmless way to be
    wrong is to answer in words - not to guess and move the mouse."""
    monkeypatch.delenv("JARVIS_DISCORD_GUILD", raising=False)
    monkeypatch.setenv(discord_bot.AGENT_ENV, "1")
    agent = FakeAgent(router=lambda text: (_ for _ in ()).throw(RuntimeError("no")))
    bot = _bot(brain=FakeBrain("Just talking."), runner=_runner(agent))

    assert bot.handle_message(_message(content="open something")) == "Just talking."
    assert agent.ran == []


def test_the_agent_asks_in_discord_and_its_next_message_is_the_answer(monkeypatch):
    """A task that needs to know something asks where it was started, and the
    next message in that channel is the answer - not a second task."""
    import threading

    monkeypatch.delenv("JARVIS_DISCORD_GUILD", raising=False)
    monkeypatch.setenv(discord_bot.AGENT_ENV, "1")
    send, agent = Recorder(), FakeAgent(asks="which report?")
    bot = _bot(send=send, runner=_runner(agent))
    bot.asker = None
    runner = bot.runner
    runner.asker = bot.ask_question
    replies: list[str] = []

    thread = threading.Thread(
        target=lambda: replies.append(
            bot.handle_message(_message(content="open the report"))))
    thread.start()
    assert _wait_until(lambda: any("❓" in body for _, body, _ in send.sent))
    assert bot._deliver_answer is not None
    answered = bot.handle_message(_message(id="1007", content="the yearly one"))

    thread.join(10)
    assert answered == "", "the answer is not itself a message to reply to"
    assert replies == ["using the yearly one"]
    bodies = [body for _, body, _ in send.sent]
    assert bodies[0].startswith("🛠")
    assert bodies[1] == "❓ which report?"
    assert bodies[2] == "using the yearly one"
    # Nothing is left waiting, so the channel is a conversation again.
    assert bot._pending == {}
    assert bot.tasks == 1


def test_only_the_next_message_is_the_answer(monkeypatch):
    """Taking *every* message while a question is open would overwrite the answer
    the task is already reading, and swallow a real instruction along with it - so
    the question claims exactly one message and then the channel is a
    conversation again. Pinned against a box we control rather than against a
    race: both readings of the same message are otherwise microseconds apart."""
    import threading

    monkeypatch.delenv("JARVIS_DISCORD_GUILD", raising=False)
    monkeypatch.setenv(discord_bot.AGENT_ENV, "1")
    send = Recorder()
    bot = _bot(brain=FakeBrain("Hello yourself."), send=send)
    box = {"event": threading.Event(), "answer": "", "done": False}
    bot._pending["2002"] = box          # what a waiting task leaves behind

    assert bot._deliver_answer(_message(content="the yearly one"), "the yearly one")
    assert box["answer"] == "the yearly one"

    assert not bot._deliver_answer(_message(content="hello"), "hello")
    assert box["answer"] == "the yearly one", "the answer was overwritten"
    # ... and it is answered on its own merits, as talk.
    assert bot.handle_message(_message(id="1008", content="hello")) == \
        "Hello yourself."
    assert [body for _, body, _ in send.sent] == ["Hello yourself."]


def test_a_question_nobody_answers_stops_the_task(monkeypatch):
    """Silence is not consent: with no answer the task ends where it asked,
    rather than the listener guessing what the person would have said."""
    monkeypatch.setenv(discord_bot.AGENT_ENV, "1")
    monkeypatch.setattr(discord_bot, "ASK_TIMEOUT", 0.2)
    send = Recorder()
    bot = _bot(send=send, runner=_runner(FakeAgent()))
    bot._turn = _message()

    assert bot.ask_question("which report?") == ""
    assert send.sent[0][1] == "❓ which report?"
    assert bot._pending == {}


def test_only_one_task_ever_has_the_desktop(monkeypatch, tmp_path):
    """Jarvis's own lock is per-process, and a console running a scheduled job is
    another process: the file is what keeps the two from fighting over the mouse."""
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path))
    agent = FakeAgent()
    runner = _runner(agent)

    with discord_bot.desktop_task() as held:      # a task already running elsewhere
        assert held
        refused = runner("open the calculator")

    assert "already doing something" in refused
    assert agent.ran == [], "a refused command must not reach the desktop"
    assert runner.refused == 1
    # Released, it is free again.
    assert runner("open the calculator") == "Done."
    assert agent.ran == ["open the calculator"]


def test_a_lock_left_by_a_task_that_died_is_stolen(monkeypatch, tmp_path):
    """A task killed mid-run leaves its lock file behind. A lock that is never
    released costs the feature; a lock stolen a little early costs a minute."""
    import os
    import time

    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path))
    path = discord_bot._task_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text("999999 1")
    os.utime(path, (time.time() - 600, time.time() - 600))
    with discord_bot.desktop_task() as stole:
        assert stole

    path.write_text("999999 1")                  # alive: heartbeating right now
    with discord_bot.desktop_task() as refused:
        assert not refused
    assert path.exists(), "a lock we did not take is not ours to delete"


def test_the_gateway_keeps_beating_while_a_task_runs(monkeypatch):
    """A command takes minutes and the heartbeat that holds the session open has
    milliseconds, so the task runs off the event loop. On it, the connection that
    is supposed to deliver the result would be dropped while producing it."""
    import asyncio
    import contextlib
    import time

    monkeypatch.delenv("JARVIS_DISCORD_GUILD", raising=False)
    monkeypatch.setenv(discord_bot.AGENT_ENV, "1")
    send = Recorder()

    class SlowAgent(FakeAgent):
        def run(self, task, asker=None):
            self.ran.append(task)
            time.sleep(0.4)
            return "Done."

    agent = SlowAgent()
    bot = _bot(send=send, runner=_runner(agent))

    class FakeWs:
        def __init__(self):
            self.sent: list[str] = []

        async def send(self, payload):
            self.sent.append(payload)

    async def scenario():
        ws = FakeWs()
        beat = asyncio.create_task(bot._heartbeat(ws, 0.02))
        await bot._frame({"op": 0, "s": 1, "t": "MESSAGE_CREATE",
                          "d": _message(content="open the calculator")})
        beats_during = len(ws.sent)
        beat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await beat
        return beats_during

    beats = asyncio.run(scenario())

    assert agent.ran == ["open the calculator"], "the task has to have run"
    assert [body for _, body, _ in send.sent][-1] == "Done."
    assert beats >= 2, ("the heartbeat was starved for the whole task: the session "
                        "would drop while the command ran")


def test_an_unconfigured_discord_says_what_to_set(monkeypatch):
    """The hint is the whole value of an unconfigured connector: it has to name
    the variable and where the value comes from."""
    monkeypatch.setattr(connectors, "_env", lambda *names: tuple("" for _ in names))
    monkeypatch.setattr(connectors, "_resolve_channel",
                        lambda target: {"id": "555", "name": target})

    try:
        connectors.fetch("discord", "send", "hello", "555")
    except connectors.ConnectorError as exc:
        assert "DISCORD_BOT_TOKEN" in str(exc)
        assert "discord.com/developers/applications" in str(exc)
    else:
        raise AssertionError("an unconfigured send must be refused")
