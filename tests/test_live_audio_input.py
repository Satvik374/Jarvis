"""A live model can accept a session and ignore the microphone.

That state is invisible from every API surface: the socket opens, the setup is
acknowledged, the audio is taken frame by frame, and the server says nothing at
all - no error, no transcript, no turn. It was measured on 2026-09-19 against a
free-tier key, where ``gemini-3.8-live`` did exactly this while the identical
clip sent to ``gemini-3.1-flash-live-preview`` came back transcribed and
answered, and a *text* turn on the silent session answered in under two seconds.

Two things follow, and both are tested here:

* the model has to be demoted and remembered, so the next session opens one that
  hears (``gemini_live._AUDIO_DEAF``), and
* the frames carrying the answer must actually be read: this endpoint sends its
  JSON in *binary* frames, and a reader that skips ``bytes`` throws away real
  transcripts and audio, which is the same symptom from the other side.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest

from jarvis.config import Config
from jarvis.live import gemini_live, verify


@pytest.fixture(autouse=True)
def _fresh_deaf_memory(tmp_path, monkeypatch):
    """Demotions outlive a process, so a test must own its own file.

    Without this the verdicts would be written to the developer's real
    ``~/.jarvis`` - and a verdict recorded by one test would silently reorder
    the next one's candidates.
    """
    monkeypatch.setenv(gemini_live._AUDIO_STATE_ENV, str(tmp_path / "live_audio.json"))
    gemini_live.clear_audio_deaf()
    yield
    gemini_live.clear_audio_deaf()


# --------------------------------------------------------------------------- #
# Remembering a model that ignores audio
# --------------------------------------------------------------------------- #

def test_one_silent_session_does_not_condemn_a_model():
    """A cold start or an unbroken sentence can look like silence for a while."""
    assert gemini_live.mark_audio_deaf("gemini-test-live") is False
    assert gemini_live.audio_deaf("gemini-test-live") is False


def test_a_verdict_survives_the_process(tmp_path, monkeypatch):
    """Learning which models hear is worthless if a restart forgets it.

    That is the whole point of writing it down: the next browser session opens on
    a model that hears instead of paying the watchdog window to relearn this.
    """
    state = tmp_path / "live_audio.json"
    monkeypatch.setenv(gemini_live._AUDIO_STATE_ENV, str(state))
    key = "test-api-key"
    gemini_live.mark_audio_deaf("gemini-test-live", "heard nothing", key=key, proven=True)
    assert state.is_file()

    # A fresh process: no memory, but the file is there.
    with patch.dict(gemini_live._AUDIO_DEAF, {}, clear=True), \
         patch.dict(gemini_live._AUDIO_SILENT_SEEN, {}, clear=True), \
         patch.dict(gemini_live._AUDIO_STATE, {}, clear=True):
        gemini_live._AUDIO_STATE_LOADED = False
        assert gemini_live.audio_deaf("gemini-test-live", key) is True
        assert "heard nothing" in gemini_live.audio_deaf_reason("gemini-test-live", key)
        assert state.read_text(encoding="utf-8")

        # Another key starts clean: the verdict is a property of the key.
        assert gemini_live.audio_deaf("gemini-test-live", "a-different-key") is False


def test_proof_demotes_a_model_immediately():
    """Silence while another model answered the same audio is not a strike."""
    assert gemini_live.mark_audio_deaf(
        "gemini-test-live", "ignored audio", key="k", proven=True
    ) is True
    assert gemini_live.audio_deaf("gemini-test-live", "k") is True


def test_a_model_that_keeps_ignoring_audio_is_demoted():
    gemini_live.mark_audio_deaf("gemini-test-live", "heard nothing")
    assert gemini_live.mark_audio_deaf("gemini-test-live", "heard nothing") is True
    assert gemini_live.audio_deaf("gemini-test-live") is True
    assert "heard nothing" in gemini_live.audio_deaf_reason("gemini-test-live")


def test_a_demoted_model_is_tried_last_rather_than_dropped():
    # Demotion is a preference, not a verdict: if nothing that hears is left,
    # the session still has somewhere to open.
    cfg = Config()
    cfg.live_voice.model = "gemini-test-live"
    for _ in range(gemini_live._DEAF_STRIKES):
        gemini_live.mark_audio_deaf("gemini-test-live")

    candidates = gemini_live.hearing_candidates(cfg.live_voice)
    assert candidates[-1] == "gemini-test-live"
    assert "gemini-test-live" not in candidates[:-1]
    assert set(candidates) == set(gemini_live.model_candidates(cfg.live_voice))


def test_a_fresh_candidate_list_keeps_the_configured_model_first():
    cfg = Config()
    cfg.live_voice.model = "gemini-configured-live"
    assert gemini_live.hearing_candidates(cfg.live_voice)[0] == "gemini-configured-live"


# --------------------------------------------------------------------------- #
# Reading the answer, however it is framed
# --------------------------------------------------------------------------- #

class _BinarySocket:
    """A `websockets` connection that answers in binary frames, as the API does."""

    def __init__(self, messages: list[dict]):
        self._messages = [json.dumps(m).encode("utf-8") for m in messages]
        self.sent: list[dict] = []

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    async def recv(self) -> bytes:
        if not self._messages:
            await asyncio.sleep(0.05)
            raise asyncio.TimeoutError
        return self._messages.pop(0)

    async def __aenter__(self) -> "_BinarySocket":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


def test_a_binary_frame_carries_the_answer():
    """The endpoint packs its JSON into binary frames; skipping them hides it."""
    socket = _BinarySocket([
        {"setupComplete": {}},
        {"serverContent": {"inputTranscription": {"text": "live voice check"}}},
        {"serverContent": {"outputTranscription": {"text": "passed"}}},
    ])
    cfg = Config()
    cfg.live_voice.google_search = "off"
    report = verify.LiveCheckReport()

    with patch("websockets.connect", return_value=socket), \
         patch.object(verify, "ANSWER_TIMEOUT", 0.3):
        asyncio.run(verify._session(cfg, b"\x00\x00" * 8000, report))

    assert report.handshake == "setupComplete"
    assert report.answered is True
    assert report.heard is True
    assert report.input_transcript == "live voice check"
    assert report.ignored_audio == []


# --------------------------------------------------------------------------- #
# The browser's two halves of the same lesson
# --------------------------------------------------------------------------- #

def test_the_relay_hands_the_page_text_never_binary():
    """`JSON.parse(blob)` throws, and the page swallows that in silence."""
    from jarvis.browser import STATIC_DIR

    source = (STATIC_DIR.parent / "browser.py").read_text(encoding="utf-8")
    assert 'raw = raw.decode("utf-8")' in source
    assert "if isinstance(raw, (bytes, bytearray)):" in source


def test_the_page_accepts_a_binary_frame_and_declares_the_binary_type():
    from jarvis.browser import STATIC_DIR

    app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert 'this.ws.binaryType = "arraybuffer";' in app
    assert 'typeof raw !== "string"' in app
    assert "new TextDecoder().decode(raw)" in app


def test_the_relay_survives_a_frame_it_cannot_decode():
    """A corrupt frame must not take the session down with it."""
    from jarvis.browser import STATIC_DIR

    source = (STATIC_DIR.parent / "browser.py").read_text(encoding="utf-8")
    assert "except UnicodeDecodeError:" in source
