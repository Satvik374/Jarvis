"""Tests for the real-session live voice check (`python run.py --check --live-check`).

The point of that command is to distinguish states that look identical from the
UI, so what matters here is that it *classifies* them: a spent allowance, a
refused setup, and a session that answers are three different outcomes with
three different remedies.
"""

from __future__ import annotations

import asyncio
import base64
import json
import struct
import wave
from io import BytesIO
from unittest.mock import patch

import pytest

from jarvis.config import Config
from jarvis.live import gemini_live, verify


# --------------------------------------------------------------------------- #
# Wire parsing
# --------------------------------------------------------------------------- #

def _report() -> verify.LiveCheckReport:
    return verify.LiveCheckReport()


def test_setup_complete_is_not_an_answer():
    report = _report()
    assert verify._describe_wire({"setupComplete": {}}, report) is False
    assert report.answered is False


def test_transcripts_audio_and_text_are_recognised():
    # The model may answer with audio only, with a transcript only, or with text;
    # any of them means the session works.
    cases = [
        ({"serverContent": {"inputTranscription": {"text": "hello"}}}, "input_transcript", "hello"),
        ({"serverContent": {"outputTranscription": {"text": "hi there"}}}, "output_transcript", "hi there"),
        ({"serverContent": {"modelTurn": {"parts": [{"text": "hi"}]}}}, "model_text", "hi"),
    ]
    for message, field_name, expected in cases:
        report = _report()
        assert verify._describe_wire(message, report) is True, message
        assert getattr(report, field_name) == expected
        assert report.answered is True

    # Spoken audio alone counts, and only its size is recorded.
    report = _report()
    payload = base64.b64encode(b"\x00\x01" * 100).decode()
    assert verify._describe_wire(
        {"serverContent": {"modelTurn": {"parts": [{"inlineData": {"data": payload}}]}}},
        report,
    ) is True
    assert report.audio_bytes == len(payload)


def test_a_tool_call_is_an_answer():
    """A voice turn that only calls a tool still proves the session works."""
    report = _report()
    message = {"toolCall": {"functionCalls": [{"name": "open_app", "args": {"name": "notepad"}}]}}
    assert verify._describe_wire(message, report) is True
    assert report.tool_calls and "open_app" in report.tool_calls[0]


def test_the_answer_can_be_output_only():
    report, _ = _run_session([
        {"setupComplete": {}},
        {"serverContent": {"outputTranscription": {"text": "passed"}}},
    ])
    assert report.answered is True
    assert report.output_transcript == "passed"
    # Not the same thing as being heard: a model that answers without
    # transcribing the input means the audio arrived as noise it then replied to.
    assert report.heard is False


def test_snake_case_wire_names_are_understood():
    report = _report()
    assert verify._describe_wire(
        {"server_content": {"input_transcription": {"text": "hey"}}}, report
    ) is True
    assert report.input_transcript == "hey"


# --------------------------------------------------------------------------- #
# The session itself
# --------------------------------------------------------------------------- #

class _FakeSocket:
    """A `websockets` connection that replays scripted messages."""

    def __init__(self, messages: list[dict]):
        self._script = [json.dumps(m) for m in messages]
        self._messages = list(self._script)
        self.sent: list[dict] = []
        #: How many connections the check opened. A session that produces
        #: nothing walks on to the next candidate model, so one scripted socket
        #: stands for every model behaving the same way.
        self.connections = 0

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    async def recv(self) -> str:
        if not self._messages:
            # Nothing more will ever arrive: behave like a quiet session.
            await asyncio.sleep(0.05)
            raise asyncio.TimeoutError
        return self._messages.pop(0)

    async def __aenter__(self) -> "_FakeSocket":
        self.connections += 1
        self._messages = list(self._script)
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _run_session(messages: list[dict], audio: bytes = b"\x00\x00" * 8000) -> _FakeSocket:
    socket = _FakeSocket(messages)
    cfg = Config()
    cfg.live_voice.google_search = "off"
    report = verify.LiveCheckReport()

    with patch("websockets.connect", return_value=socket), \
         patch.object(verify, "ANSWER_TIMEOUT", 0.3):
        asyncio.run(verify._session(cfg, audio, report))
    return report, socket


def test_a_spent_allowance_is_reported_as_a_quota_state():
    """The refusal this key really returns, verbatim.

    It is the *same* sentence grounding refusals produce, which is why the check
    has to label it rather than pass the text along: the remedy differences
    matter, and no local setting fixes a spent quota.
    """
    refusal = {
        "error": {
            "code": 1011,
            "message": (
                "You exceeded your current quota, please check your plan and billing "
                "details."
            ),
            "status": "INTERNAL",
        }
    }
    report, _ = _run_session([refusal])
    assert report.quota is True
    assert report.answered is False
    assert report.handshake == "refused"
    assert "quota" in report.error.lower()


def test_a_working_session_reports_what_the_model_said():
    # The first real evidence ends the wait: the check exists to answer "does a
    # session work here", not to transcribe the phrase, and the free tier pays
    # for every extra second of session.
    report, socket = _run_session([
        {"setupComplete": {}},
        {"serverContent": {"inputTranscription": {"text": "live voice check"}}},
        {"serverContent": {"outputTranscription": {"text": "passed"}}},
    ])
    assert report.handshake == "setupComplete"
    assert report.quota is False
    assert report.answered is True
    assert report.heard is True
    assert report.input_transcript == "live voice check"

    # The audio was streamed the way the page streams it - 16 kHz PCM, and the
    # turn ended explicitly so "no answer" cannot mean "still listening".
    audio = [m for m in socket.sent if "realtimeInput" in m and "audio" in m["realtimeInput"]]
    assert audio, socket.sent
    assert audio[0]["realtimeInput"]["audio"]["mimeType"] == "audio/pcm;rate=16000"
    assert socket.sent[-1] == {"realtimeInput": {"audioStreamEnd": True}}


def test_a_silent_model_session_is_not_confused_with_a_failure_to_connect():
    """A session that opens and then says nothing is not a broken connection.

    It is a model that took the audio and ignored it (see
    ``gemini_live._AUDIO_DEAF``), which no local setting reports and which the
    API answers with a perfectly healthy ``setupComplete``. The check records
    it and moves to the next candidate, so the verdict names the model rather
    than the network.
    """
    report, socket = _run_session([{"setupComplete": {}}])
    assert report.handshake == "setupComplete"
    assert report.answered is False
    assert report.quota is False
    assert report.error == ""
    # Every candidate accepted a session and said nothing, so every one of them
    # is named, and the check says so instead of blaming the account.
    assert report.ignored_audio == gemini_live.hearing_candidates(Config().live_voice)
    assert socket.connections == len(report.ignored_audio) >= 2


def test_a_missing_key_is_reported_without_opening_anything():
    cfg = Config()
    report = verify.LiveCheckReport()
    with patch.object(verify.readiness, "gemini_api_key", return_value=""):
        asyncio.run(verify._session(cfg, b"\x00\x00" * 1000, report))
    assert "no Gemini API key" in report.error
    assert report.answered is False


# --------------------------------------------------------------------------- #
# The audio it sends
# --------------------------------------------------------------------------- #

def _wav_bytes(rate: int, channels: int, samples: list[float]) -> bytes:
    frames = bytearray()
    for value in samples:
        packed = struct.pack("<h", int(value * 32767))
        frames += packed * channels
    buf = BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(bytes(frames))
    return buf.getvalue()


def test_stereo_44k_audio_is_rewritten_as_16k_mono(tmp_path):
    """A recording the user makes is rarely in the format the API accepts."""
    import math

    samples = [math.sin(i / 20.0) for i in range(44100)]
    path = tmp_path / "recording.wav"
    path.write_bytes(_wav_bytes(44100, 2, samples))

    pcm = verify._wav_to_pcm16k(str(path))
    assert len(pcm) == pytest.approx(16000 * 2, rel=0.02)
    assert len(pcm) % 2 == 0


def test_the_probe_phrase_falls_back_to_an_error_that_names_the_fix():
    """With no TTS engine installed the check must say what to pass instead."""
    with patch("jarvis.utils.voice.speak_to_wav", return_value=False):
        with pytest.raises(RuntimeError) as caught:
            verify._probe_audio(None)
    assert "--live-audio" in str(caught.value)


def test_a_missing_recording_is_reported_before_anything_is_sent():
    with pytest.raises(FileNotFoundError):
        verify._probe_audio("/definitely/not/here.wav")
