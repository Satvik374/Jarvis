"""Behavioural tests for the away assistant (``jarvis.whatsapp_away``).

The words a caller receives come from the model at runtime, so a fake brain
stands in for the provider: what these tests pin is what the automation does with
the answer - who it replies to, in whose words, what it captures, what it
reports, and what it refuses to do when the provider is unavailable. Nothing here
talks to WhatsApp, and the inbox, the outbox and the store are all per-test
files, so no real state is read or written.
"""

from __future__ import annotations

import json
import os
import pathlib
import time

import pytest

from jarvis import whatsapp_away as away
from jarvis.tools import connectors


class FakeBrain:
    """A provider stand-in that records its prompts and replays given replies."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts: list[tuple[str, list[dict]]] = []

    def complete(self, system, messages, image=None):
        self.prompts.append((system, messages))
        return self.replies.pop(0) if self.replies else "Thanks, I will pass it on."


class DeadBrain:
    """A provider that is not reachable at all."""

    def __init__(self, message="the AI service is busy"):
        self.message = message

    def complete(self, system, messages, image=None):
        raise RuntimeError(self.message)


@pytest.fixture()
def store(tmp_path):
    return away.Away(state=tmp_path / "away.json", inbox=tmp_path / "inbox.jsonl",
                     outbox=tmp_path / "outbox.jsonl")


def _wait_for(condition, timeout: float = 8.0) -> bool:
    """Wait for something a background thread does; no fixed sleep, no race."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return condition()


def _terminate_event(wa_id="919812345678", call_id="wacid.1") -> dict:
    """The Call Terminate webhook, in Meta's shape."""
    return {"object": "whatsapp_business_account", "entry": [{"id": "0", "changes": [
        {"field": "calls", "value": {
            "messaging_product": "whatsapp",
            "contacts": [{"wa_id": wa_id, "profile": {"name": "Ravi Kumar"}}],
            "calls": [{"id": call_id, "from": wa_id, "to": "0",
                       "event": "terminate", "direction": "USER_INITIATED",
                       "timestamp": "1749197480", "start_time": "1749197000",
                       "end_time": "1749197480", "duration": 480}]}}]}],
        }


# --------------------------------------------------------------------------- #
# the four behaviours
# --------------------------------------------------------------------------- #

def test_a_ringing_call_is_answered_by_message_in_the_models_own_words(store):
    brain = FakeBrain("Ravi, hello - he is busy just now. What would you like me "
                      "to pass on to him?")
    store.driver().caller_dials("919812345678", "Ravi Kumar")

    summary = away.poll_and_answer(brain=brain, away=store)

    sent = store.driver().replies()
    assert len(sent) == 1
    assert sent[0]["to"] == "919812345678"
    # The text is the model's, not a canned line: nothing else in the module
    # holds this sentence.
    assert sent[0]["body"] == ("Ravi, hello - he is busy just now. What would you "
                               "like me to pass on to him?")
    assert "Ravi Kumar" in summary
    # The instruction asks for both halves the request named.
    system, messages = brain.prompts[0]
    assert "busy" in system and "pass on" in system
    assert "called" in messages[0]["content"]


def test_what_the_caller_wants_passed_on_is_captured_and_reported(store):
    brain = FakeBrain("He is busy - what can I pass on?",
                      "Noted, I will pass that on. Thank you!")
    driver = store.driver()
    driver.caller_dials("919812345678", "Ravi Kumar")
    away.poll_and_answer(brain=brain, away=store)

    driver.caller_says("919812345678", "Tell him the gate code is 4417.", "Ravi Kumar")
    away.poll_and_answer(brain=brain, away=store)

    report = store.report(mark=True)
    assert "Ravi Kumar" in report          # who
    assert "4417" in report                # what they wanted
    assert "called" in report
    assert "left word" in report
    # Acknowledged, so the caller is not left hanging.
    assert len(store.driver().replies()) == 2


def test_a_caller_who_writes_the_whole_message_in_one_go_has_it_captured(store):
    """Somebody who simply types the thing must not be asked to type it again.

    They used to be: a first message was answered with "what would you like to
    pass on?", their words were never captured and never even shown to the model.
    """
    brain = FakeBrain("He is busy just now - I will pass that on for you.")
    store.driver().caller_says("447700900123", "hi, is the AMC visit still on?",
                               "Ravi")

    away.poll_and_answer(brain=brain, away=store)

    assert len(store.driver().replies()) == 1          # acknowledged, not interrogated
    system, messages = brain.prompts[0]
    assert "busy" in system and "pass it on" in system
    assert "AMC" in messages[0]["content"]             # their words reached the model

    report = store.report()
    assert "Ravi" in report and "AMC" in report        # and the user's report
    assert "1 WhatsApp caller left word" in report
    assert "cannot be answered" not in report          # no call was involved


def test_a_message_with_no_words_is_asked_the_same_way_a_call_is(store):
    """A photo or a voice note carries nothing to pass on, so asking is right."""
    brain = FakeBrain("He is busy - what can I pass on?")
    store.driver().caller_says("447700900123", "", "Ravi")

    away.poll_and_answer(brain=brain, away=store)

    assert "ask what the caller" in brain.prompts[0][0]
    assert "left no message" in store.report()         # the ring still reaches you


def test_only_the_report_says_calls_were_not_answered_by_voice(store):
    brain = FakeBrain("He is busy - what can I pass on?", "Noted, thanks!")
    driver = store.driver()
    driver.caller_dials("919812345678", "Ravi Kumar")
    away.poll_and_answer(brain=brain, away=store)
    driver.caller_says("919812345678", "the AMC visit - is it still on?", "Ravi Kumar")
    away.poll_and_answer(brain=brain, away=store)

    report = store.report()
    assert "cannot be answered" in report
    # The message the caller received must not claim the call was picked up.
    for reply in store.driver().replies():
        assert "answered" not in reply["body"].lower()
        assert "picked up" not in reply["body"].lower()


def test_marking_the_report_consumes_it(store):
    brain = FakeBrain("He is busy - what can I pass on?", "Noted, thanks!")
    driver = store.driver()
    driver.caller_dials("1", "Ravi")
    away.poll_and_answer(brain=brain, away=store)
    driver.caller_says("1", "the spare key", "Ravi")
    away.poll_and_answer(brain=brain, away=store)

    assert store.report(mark=True) != ""
    assert store.report() == ""


# --------------------------------------------------------------------------- #
# what must not happen
# --------------------------------------------------------------------------- #

def test_a_call_that_ended_is_not_answered_again(store):
    path = store.inbox_path()
    path.write_text(json.dumps(_terminate_event()) + "\n", encoding="utf-8")
    brain = FakeBrain("should never be sent")

    assert store.driver().replies() == []
    assert away.poll_and_answer(brain=brain, away=store) == ""
    assert store.driver().replies() == []
    assert brain.prompts == []


def test_a_dead_provider_sends_nothing_and_never_substitutes_a_canned_line(store):
    store.driver().caller_dials("919812345678", "Ravi Kumar")

    summary = away.poll_and_answer(brain=DeadBrain(), away=store)

    assert store.driver().replies() == []
    report = store.report()
    assert "could not be answered" in report
    assert "busy" in report                      # the reason, in plain words
    assert "Ravi Kumar" in summary


def test_the_caller_a_dead_provider_missed_is_retried_on_the_next_pass(store, monkeypatch):
    monkeypatch.setattr(away, "FAILURE_RETRY", 0.0)      # the wait window has passed
    store.driver().caller_dials("919812345678", "Ravi Kumar")
    away.poll_and_answer(brain=DeadBrain(), away=store)

    brain = FakeBrain("He is busy - what can I pass on?")
    away.poll_and_answer(brain=brain, away=store)

    assert len(store.driver().replies()) == 1    # exactly one, on the retry


def test_a_caller_that_just_failed_is_not_retried_on_every_poll(store):
    """The live watch polls every couple of seconds. Without the wait window an
    unreachable provider is hit on every single poll."""
    store.driver().caller_dials("919812345678", "Ravi Kumar")
    away.poll_and_answer(brain=DeadBrain(), away=store)

    class Counting(DeadBrain):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def complete(self, system, messages, image=None):
            self.calls += 1
            return super().complete(system, messages, image)

    counting = Counting()
    away.poll_and_answer(brain=counting, away=store)

    assert counting.calls == 0                   # skipped, not retried
    assert store.report() != ""                  # and still reported honestly


def test_the_same_event_is_answered_once_however_often_it_is_polled(store):
    brain = FakeBrain("He is busy - what can I pass on?")
    store.driver().caller_dials("919812345678", "Ravi")

    away.poll_and_answer(brain=brain, away=store)
    away.poll_and_answer(brain=brain, away=store)
    away.poll_and_answer(brain=brain, away=store)

    assert len(store.driver().replies()) == 1
    assert len(brain.prompts) == 1


def test_a_refused_platform_send_is_not_reported_as_delivered(store):
    store.driver().caller_dials("919812345678", "Ravi")
    brain = FakeBrain("He is busy - what can I pass on?")
    refusal = connectors.ConnectorError(
        "WhatsApp send -> HTTP 400: outside the 24-hour window")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(connectors, "whatsapp_can_send", lambda: True)
        patch.setattr(connectors, "whatsapp_send_text",
                      lambda to, body: (_ for _ in ()).throw(refusal))
        away.poll_and_answer(brain=brain, away=store)

    assert store.driver().replies() == []        # nothing claimed a delivery
    assert "24-hour" in store.report()
    # Not marked handled, so the platform refusal is retried rather than dropped.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(connectors, "whatsapp_can_send", lambda: True)
        patch.setattr(connectors, "whatsapp_send_text", lambda to, body: "wamid.1")
        away.poll_and_answer(brain=FakeBrain("He is busy - what can I pass on?"),
                             away=store)


# --------------------------------------------------------------------------- #
# the two transports behind one seam
# --------------------------------------------------------------------------- #

def test_the_cloud_api_sends_when_tokens_exist_and_the_bridge_stays_empty(store):
    store.driver().caller_dials("919812345678", "Ravi Kumar")
    brain = FakeBrain("He is busy - what can I pass on?")
    calls: list[tuple[str, str]] = []

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(connectors, "whatsapp_can_send", lambda: True)
        patch.setattr(connectors, "whatsapp_send_text",
                      lambda to, body: calls.append((to, body)) or "wamid.1")
        summary = away.poll_and_answer(brain=brain, away=store)

    assert calls == [("919812345678", "He is busy - what can I pass on?")]
    assert store.driver().replies() == []
    assert "local bridge" not in summary


def test_the_local_bridge_is_named_as_the_delivery_route_when_it_is(store):
    store.driver().caller_dials("919812345678", "Ravi Kumar")

    summary = away.poll_and_answer(
        brain=FakeBrain("He is busy - what can I pass on?"), away=store)

    assert "local bridge" in summary
    assert str(store.outbox_path) in summary
    assert len(store.driver().replies()) == 1


# --------------------------------------------------------------------------- #
# while the application is running (what makes this an automation)
# --------------------------------------------------------------------------- #

@pytest.fixture()
def watch(store):
    """A live watch on a fast interval, stopped however the test ends."""
    made: list[away.LiveWatch] = []

    def _make(brain, interval: float = 0.02) -> away.LiveWatch:
        watcher = away.LiveWatch(cfg=None, brain=brain, away=store,
                                 interval=interval)
        made.append(watcher)
        return watcher

    yield _make
    for watcher in made:
        watcher.stop()


def test_a_caller_arriving_while_the_app_runs_is_answered_without_a_restart(store, watch):
    """No manual poll and no restart: the running app serves the caller."""
    brain = FakeBrain("He is busy just now - what can I pass on?",
                      "Noted, I will pass that on.")
    assert watch(brain).start() is True

    store.driver().caller_dials("919812345678", "Ravi Kumar")

    assert _wait_for(lambda: len(store.driver().replies()) == 1)
    assert store.driver().replies()[0]["body"] == "He is busy just now - what can I pass on?"

    store.driver().caller_says("919812345678", "the gate code is 4417", "Ravi Kumar")

    assert _wait_for(lambda: len(store.load().get("relays", [])) == 1)
    assert store.load()["relays"][0]["text"] == "the gate code is 4417"


def test_the_live_watch_tells_you_as_it_happens(store, watch, monkeypatch):
    """The notice goes out while the app stays up - that is the report surface."""
    notices: list[str] = []
    monkeypatch.setattr(away.log, "proactive",
                        lambda rule_name, message, title="", event_type="":
                        notices.append(message))
    brain = FakeBrain("He is busy - what can I pass on?", "Noted, thanks!")
    assert watch(brain).start() is True

    store.driver().caller_dials("919812345678", "Ravi Kumar")
    assert _wait_for(lambda: len(store.driver().replies()) == 1)
    store.driver().caller_says("919812345678", "the gate code is 4417", "Ravi Kumar")

    assert _wait_for(lambda: any("4417" in n for n in notices))
    line = [n for n in notices if "4417" in n][0]
    assert "Ravi Kumar" in line and "called" in line


def test_a_caller_who_rings_and_says_nothing_is_still_told_to_you(store, watch, monkeypatch):
    """A ring that never becomes a message must not be invisible."""
    notices: list[str] = []
    monkeypatch.setattr(away.log, "proactive",
                        lambda rule_name, message, title="", event_type="":
                        notices.append(message))
    assert watch(FakeBrain("He is busy - what can I pass on?")).start() is True

    store.driver().caller_dials("919812345678", "Ravi Kumar")

    assert _wait_for(lambda: any("Ravi Kumar" in n for n in notices))
    line = [n for n in notices if "Ravi Kumar" in n][0]
    assert "called" in line and "busy" in line
    assert store.report() == ""      # told live, so the next start stays quiet
    assert "Ravi Kumar" in store.session_report()      # and :status still shows it


def test_a_ring_with_no_words_reaches_the_report_at_the_next_start(store):
    """The caller rang while Jarvis was closed and hung up. The greeting has to
    name them, or the fact that somebody tried to reach you is simply lost."""
    store.driver().caller_dials("919812345678", "Ravi Kumar")

    away.poll_and_answer(brain=FakeBrain("He is busy - what can I pass on?"),
                         away=store)

    report = store.report()
    assert "Ravi Kumar" in report
    assert "called" in report and "left no message" in report
    assert "cannot be answered" in report        # a call, so the plain truth about it


def test_the_words_replace_the_note_that_they_rang(store):
    """Rang at 12:00, left word at 12:05: one caller, one fact - not two lines."""
    brain = FakeBrain("He is busy - what can I pass on?", "Noted, thanks!")
    driver = store.driver()
    driver.caller_dials("919812345678", "Ravi Kumar")
    away.poll_and_answer(brain=brain, away=store)
    driver.caller_says("919812345678", "the gate code is 4417", "Ravi Kumar")
    away.poll_and_answer(brain=brain, away=store)

    report = store.report()
    assert "left no message" not in report
    assert "4417" in report


def test_one_caller_who_writes_twice_is_one_caller_with_two_things_to_pass_on(store):
    """The count has to be about people, not about messages."""
    brain = FakeBrain("He is busy - what can I pass on?", "Noted, thanks!",
                      "Noted, thanks again!")
    driver = store.driver()
    driver.caller_dials("919812345678", "Ravi Kumar")
    away.poll_and_answer(brain=brain, away=store)
    driver.caller_says("919812345678", "the gate code is 4417", "Ravi Kumar")
    away.poll_and_answer(brain=brain, away=store)
    driver.caller_says("919812345678", "also, the AMC visit is off", "Ravi Kumar")
    away.poll_and_answer(brain=brain, away=store)

    report = store.report()
    assert "1 WhatsApp caller left word" in report     # not "2 callers"
    assert report.count("Ravi Kumar") == 1             # one line for one person
    assert "4417" in report and "AMC" in report        # nothing they said was lost


def test_a_notice_never_claims_a_delivery_that_never_left_the_machine(store, watch,
                                                                     monkeypatch):
    """On the local bridge the reply only reaches the outbox, so the notice says
    so instead of telling the user the caller was told."""
    notices: list[str] = []
    monkeypatch.setattr(away.log, "proactive",
                        lambda rule_name, message, title="", event_type="":
                        notices.append(message))
    assert watch(FakeBrain("He is busy - what can I pass on?")).start() is True

    store.driver().caller_dials("919812345678", "Ravi Kumar")

    assert _wait_for(lambda: any("Ravi Kumar" in n for n in notices))
    line = [n for n in notices if "Ravi Kumar" in n][0]
    assert "not sent" in line
    assert "I told them" not in line


# --------------------------------------------------------------------------- #
# the state file itself: a save that cannot land, and content that cannot be read
# --------------------------------------------------------------------------- #

def _refuse_every_replace(monkeypatch):
    """What Windows does while something else holds the state file open."""
    real = os.replace
    monkeypatch.setattr(away.os, "replace",
                        lambda src, dst: (_ for _ in ()).throw(
                            PermissionError(13, "the file is in use")))
    return real


def test_a_save_that_cannot_land_does_not_tell_the_caller_twice(store, monkeypatch):
    """The state file is held open, so the write cannot land - and the caller who
    has already been messaged must not be messaged again for it."""
    store.save({"handled": [], "callers": {}, "relays": [], "rings": [],
                "failed": []})
    brain = FakeBrain("He is busy - what can I pass on?")
    store.driver().caller_dials("919812345678", "Ravi Kumar")
    real = _refuse_every_replace(monkeypatch)

    away.poll_and_answer(brain=brain, away=store)
    away.poll_and_answer(brain=brain, away=store)
    away.poll_and_answer(brain=brain, away=store)

    assert len(store.driver().replies()) == 1        # told once, never twice
    assert store.spill_path.exists()                 # the record did survive
    assert store.load()["handled"]                   # and the app sees it as done
    assert not list(store.state_path.parent.glob("*.tmp"))    # no temp litter

    monkeypatch.setattr(away.os, "replace", real)   # the file is free again
    away.poll_and_answer(brain=brain, away=store)

    assert not store.spill_path.exists()             # folded back where it belongs
    assert json.loads(store.state_path.read_text())["handled"]
    assert len(store.driver().replies()) == 1


def test_a_pass_carries_on_to_the_next_caller_when_a_save_cannot_land(store,
                                                                     monkeypatch):
    """One caller's refused save must not take the rest of the pass down with it."""
    brain = FakeBrain("He is busy - what can I pass on?")
    store.driver().caller_dials("9111", "Ravi")
    store.driver().caller_dials("9222", "Meena")
    _refuse_every_replace(monkeypatch)

    summary = away.poll_and_answer(brain=brain, away=store)

    assert len(store.driver().replies()) == 2        # both served anyway
    assert "Ravi" in summary and "Meena" in summary


def test_an_unreadable_state_is_set_aside_and_said_plainly(store, tmp_path):
    """Half-written content used to be a silent omission; it is now a sentence."""
    store.state_path.write_text('{"relays": [{"who": "Ravi"', encoding="utf-8")

    report = store.report()

    assert "could not be read" in report and "set aside" in report
    assert list(tmp_path.glob("*.corrupt-*")), "the file is kept, not overwritten"
    store.driver().caller_dials("919812345678", "Ravi Kumar")
    away.poll_and_answer(brain=FakeBrain("He is busy - what can I pass on?"),
                         away=store)
    assert len(store.driver().replies()) == 1        # and the store still works


def test_a_hand_edited_state_does_not_break_the_report_or_a_pass(store):
    """Wrong shapes are repaired field by field and reported, not trusted -
    both a field of the wrong type, and entries of the wrong type inside one."""
    store.state_path.write_text(
        '{"relays": [1, "x"], "handled": [7, 8], "rings": "oops", '
        '"callers": {"9198": "not a dict"}}', encoding="utf-8")

    report = store.report()                          # used to raise AttributeError

    assert "unreadable" in report                    # and it is never silent
    store.driver().caller_dials("919812345678", "Ravi Kumar")
    away.poll_and_answer(brain=FakeBrain("He is busy - what can I pass on?"),
                         away=store)
    assert len(store.driver().replies()) == 1
    assert "Ravi Kumar" in store.report()
    # Said once, and kept for the start that has not happened yet.
    assert json.loads(store.state_path.read_text())["damage"]


def test_the_unreadable_note_is_said_once(store):
    """Seen, then persisted by a save, then said at the start - and never again.

    That order matters: the copy on file is the one the greeting consumes, so the
    note must not be put back just because that copy now carries the mark.
    """
    store.state_path.write_text('{"relays": "oops", "handled": [], "callers": {}}',
                                encoding="utf-8")
    assert "unreadable" in store.report()            # seen before the start
    store.driver().caller_dials("919812345678", "Ravi Kumar")
    away.poll_and_answer(brain=FakeBrain("He is busy - what can I pass on?"),
                         away=store)                 # persisted by a save

    assert "unreadable" in store.report(mark=True)    # the start says it
    assert store.report() == ""                       # and then it is quiet


def test_a_stale_spill_does_not_beat_a_newer_state_file(store):
    """The spill wins only while it is genuinely the newer copy."""
    store.save({"handled": ["old"], "callers": {}, "relays": [], "rings": [],
                "failed": []})
    store.spill_path.write_text(json.dumps({"handled": ["stale"]}), encoding="utf-8")
    long_ago = store.state_path.stat().st_mtime - 60
    os.utime(store.spill_path, (long_ago, long_ago))

    assert store.load()["handled"] == ["old"]


def test_the_dashboard_line_for_one_caller_stays_one_line(store, watch, monkeypatch):
    """A second message updates that caller's dashboard line, not the list."""
    monkeypatch.setattr(away.log, "proactive", lambda *a, **k: None)
    brain = FakeBrain("He is busy - what can I pass on?", "Noted!", "Noted again!")
    assert watch(brain).start() is True

    store.driver().caller_says("9198", "the gate code is 4417", "Ravi")
    assert _wait_for(lambda: "4417" in store.session_report())
    store.driver().caller_says("9198", "also the AMC visit is off", "Ravi")
    assert _wait_for(lambda: "AMC" in store.session_report())

    line = store.session_report()
    assert line.count("Ravi") == 1          # one caller, one line
    assert "4417" in line and "AMC" in line  # and nothing they said was dropped


def test_a_call_event_with_no_number_is_not_answered_or_reported(store):
    """A reply needs somebody to reply to; an event with no number is not one."""
    store.inbox_path().write_text(json.dumps(
        {"object": "whatsapp_business_account", "entry": [{"id": "0", "changes": [
            {"field": "calls", "value": {"calls": [
                {"id": "wacid.1", "event": "connect",
                 "timestamp": "1749197480"}]}}]}]}), encoding="utf-8")
    brain = FakeBrain("should never be sent")

    assert away.poll_and_answer(brain=brain, away=store) == ""
    assert store.driver().replies() == []
    assert brain.prompts == []
    assert store.report() == ""
    assert "?" not in json.dumps(store.load())


def test_a_caller_announced_live_is_not_repeated_by_the_next_briefing(store, watch, monkeypatch):
    monkeypatch.setattr(away.log, "proactive",
                        lambda *a, **k: None)
    brain = FakeBrain("He is busy - what can I pass on?", "Noted, thanks!")
    assert watch(brain).start() is True

    store.driver().caller_dials("919812345678", "Ravi Kumar")
    assert _wait_for(lambda: len(store.driver().replies()) == 1)
    store.driver().caller_says("919812345678", "the gate code is 4417", "Ravi Kumar")
    # The record is stored first and announced a moment later; the announcement
    # is what marks it, so wait for that rather than for the record. One read per
    # poll: the state is replaced as it is saved, so two reads could disagree.
    def announced() -> bool:
        relays = store.load().get("relays") or []
        return bool(relays) and relays[0].get("reported") is True

    assert _wait_for(announced)

    assert store.report() == ""      # told once, live; not again at startup


def test_a_caller_we_could_not_answer_is_announced_live_too(store, watch, monkeypatch):
    """Silence is not an option for the live path either."""
    notices: list[str] = []
    monkeypatch.setattr(away.log, "proactive",
                        lambda rule_name, message, title="", event_type="":
                        notices.append(message))
    assert watch(DeadBrain("the AI service is busy")).start() is True

    store.driver().caller_dials("919812345678", "Ravi Kumar")

    assert _wait_for(lambda: any("could not answer" in n for n in notices))
    assert "Ravi Kumar" in [n for n in notices if "could not answer" in n][0]
    assert store.driver().replies() == []


def test_the_watch_is_not_started_when_there_is_nothing_to_watch(tmp_path, monkeypatch):
    """No inbound file means no source, so no thread is started at all."""
    monkeypatch.setattr(connectors, "whatsapp_inbox_path", lambda: None)
    quiet = away.Away(state=tmp_path / "s.json", inbox=None,
                      outbox=tmp_path / "o.jsonl")
    watcher = away.LiveWatch(cfg=None, brain=FakeBrain(), away=quiet, interval=0.02)

    assert watcher.start() is False
    assert watcher.running is False


def test_starting_the_same_watch_twice_does_not_stack_threads(store, watch):
    watcher = watch(FakeBrain("He is busy - what can I pass on?"))

    assert watcher.start() is True
    assert watcher.start() is False
    watcher.stop()
    assert watcher.running is False


# --------------------------------------------------------------------------- #
# the state file the watch and the report both read
# --------------------------------------------------------------------------- #

def test_a_refused_replace_is_retried_rather_than_losing_a_caller(store, monkeypatch):
    """Keeping the state is how the app knows who is on file.

    Saving replaces that file, and on Windows the replace is refused for an instant
    while a reader has it open - the live watch, the report and the startup pass all
    read it - so a refusal has to be waited out rather than the write being dropped.
    """
    real_replace = os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] <= 3:
            raise PermissionError(13, "the file is in use")
        return real_replace(src, dst)

    monkeypatch.setattr(away.os, "replace", flaky)
    store.save({"relays": [{"key": "m:1", "who": "Ravi Kumar"}]})

    assert calls["n"] == 4                     # refused three times, then landed
    assert store.load()["relays"][0]["who"] == "Ravi Kumar"
    assert not store.state_path.with_name(store.state_path.name + ".tmp").exists()


def test_a_read_that_races_a_save_is_not_called_empty(store, monkeypatch):
    """The same window from the reader's side: 'could not read' is not 'nobody
    called', and a caller on file must not look absent for an instant."""
    store.save({"relays": [{"key": "m:1", "who": "Ravi Kumar"}]})
    real_read = pathlib.Path.read_text
    calls = {"n": 0}

    def flaky(self, *args, **kwargs):
        if self == store.state_path and calls["n"] == 0:
            calls["n"] += 1
            raise PermissionError(13, "the file is in use")
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", flaky)

    assert store.load()["relays"][0]["who"] == "Ravi Kumar"

