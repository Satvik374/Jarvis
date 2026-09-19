"""Open a real Gemini Live session and report whether the model answers.

Everything else in this package can be checked by reading configuration, and
that is exactly the gap that makes a silent voice session so hard to debug: a
spent allowance, a retired model, an audio format the model cannot use and a
microphone that is not being heard all produce the same symptom - the UI says
LISTENING and nothing is ever said back.

This does what the browser does, with no browser and no microphone: it opens a
session with the same setup message, streams a synthesized phrase as 16 kHz PCM
the way the page streams yours, ends the turn, and reports what came back. So a
failure here is the account, the model or the session; a success here with a
silent browser points at the page's own audio.

    python run.py --live-check
    python run.py --live-check --live-audio my-recording.wav
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import tempfile
import wave
from dataclasses import dataclass, field
from typing import Any

from ..utils import logging as log
from . import gemini_live, readiness

#: What the model is asked to do with the phrase it hears. Anything the session
#: returns at all is the signal, so this only has to be easy to say out loud.
PROBE_PHRASE = "Jarvis, reply with the words live voice check passed."

#: How long to wait for the model to answer after the phrase ends. The Live API
#: routinely takes a few seconds; a free-tier key longer.
ANSWER_TIMEOUT = 30.0

#: Audio is streamed in real time, because that is how the page streams it and
#: the model's turn detection is sensitive to it.
CHUNK_MS = 250


@dataclass
class LiveCheckReport:
    """What the session actually did, for the caller to print."""

    model: str = ""
    voice: str = ""
    engine: str = ""
    handshake: str = ""
    grounding: str = ""
    notice: str = ""
    input_transcript: str = ""
    output_transcript: str = ""
    model_text: str = ""
    audio_bytes: int = 0
    tool_calls: list[str] = field(default_factory=list)
    error: str = ""
    #: Models that accepted the handshake and then produced nothing at all for
    #: the audio that was streamed to them. A session like that is silent in a
    #: way the API never reports, so it is recorded rather than returned as a
    #: plain failure - and the check carries on to the next model that hears.
    ignored_audio: list[str] = field(default_factory=list)
    #: True when the refusal is an allowance state rather than a bad key or a bad
    #: model name. The API's wording is the same generic quota sentence either
    #: way, but the remedy is not: nothing on this machine can fix a spent quota,
    #: and everything else can.
    quota: bool = False

    @property
    def answered(self) -> bool:
        return bool(
            self.input_transcript or self.output_transcript or self.model_text
            or self.audio_bytes or self.tool_calls
        )

    @property
    def heard(self) -> bool:
        return bool(self.input_transcript)


def _wav_to_pcm16k(path: str) -> bytes:
    """Read a WAV as 16 kHz mono 16-bit PCM, which is what the API accepts."""
    with wave.open(path) as wav:
        frames = wav.readframes(wav.getnframes())
        rate, width, channels = wav.getframerate(), wav.getsampwidth(), wav.getnchannels()

    if channels > 1 or width != 2:
        import audioop

        if channels > 1:
            frames = audioop.tomono(frames, width, 0.5, 0.5)
        if width != 2:
            frames = audioop.lin2lin(frames, width, 2)
    if rate != gemini_live.PCM_IN_RATE:
        import audioop

        frames, _ = audioop.ratecv(frames, 2, 1, rate, gemini_live.PCM_IN_RATE, None)
    return frames


def _probe_audio(audio_path: str | None) -> tuple[bytes, str]:
    """The phrase to speak: the caller's own recording, or synthesized speech."""
    if audio_path:
        if not os.path.isfile(audio_path):
            raise FileNotFoundError(audio_path)
        return _wav_to_pcm16k(audio_path), os.path.basename(audio_path)

    from ..utils import voice

    target = os.path.join(tempfile.gettempdir(), "jarvis_live_check.wav")
    if not voice.speak_to_wav(PROBE_PHRASE, target):
        raise RuntimeError(
            "could not synthesize the probe phrase; install a TTS engine (kokoro, "
            "Windows SAPI) or pass --live-audio <your own .wav>"
        )
    return _wav_to_pcm16k(target), "synthesized phrase"


def _describe_wire(message: dict[str, Any], report: LiveCheckReport) -> bool:
    """Fold one server message into the report. True if it was model output."""
    for key in ("setupComplete", "setup_complete"):
        if key in message:
            return False

    server = message.get("serverContent") or message.get("server_content")
    if isinstance(server, dict):
        for key in ("inputTranscription", "input_transcription"):
            chunk = server.get(key) or {}
            if chunk.get("text"):
                report.input_transcript += str(chunk["text"])
        for key in ("outputTranscription", "output_transcription"):
            chunk = server.get(key) or {}
            if chunk.get("text"):
                report.output_transcript += str(chunk["text"])
        turn = server.get("modelTurn") or server.get("model_turn") or {}
        for part in turn.get("parts") or []:
            if part.get("text"):
                report.model_text += str(part["text"])
            inline = part.get("inlineData") or part.get("inline_data") or {}
            if inline.get("data"):
                report.audio_bytes += len(str(inline["data"]))
        return bool(report.input_transcript or report.output_transcript
                    or report.model_text or report.audio_bytes)

    call = message.get("toolCall") or message.get("tool_call")
    if isinstance(call, dict):
        for entry in call.get("functionCalls") or call.get("function_calls") or []:
            report.tool_calls.append(
                f"{entry.get('name')}({json.dumps(entry.get('args') or {})[:80]})"
            )
        return bool(report.tool_calls)
    return False


async def _session(cfg: Any, audio: bytes, report: LiveCheckReport) -> None:
    import websockets

    live = cfg.live_voice
    key = readiness.gemini_api_key(cfg)
    if not key:
        report.error = "no Gemini API key is configured (JARVIS_LIVE_API_KEY / vault)"
        return

    models = gemini_live.hearing_candidates(live, key)
    grounding = gemini_live.grounding_wanted(live)
    # Same policy as the relay: a refused search tool costs the whole handshake,
    # so it is retried once without grounding before anything is called broken.
    # Every other candidate is then tried too, because a model can accept the
    # session and still ignore audio - the one failure the API does not report.
    attempts: list[tuple[str, bool]] = []
    if grounding:
        attempts.append((models[0], True))
    attempts.extend((name, False) for name in models)
    uri = f"{gemini_live.GEMINI_LIVE_WS}?key={key}"
    setup_refusal = ""

    def _is_quota(text: str) -> bool:
        if gemini_live.quota_refusal(text):
            report.quota = True
            return True
        return False

    for model, search in attempts:
        setup = gemini_live.build_setup(
            live,
            model=model,
            system_instruction=(
                "You are verifying a live voice link. Say exactly what you are asked "
                "to say, and nothing else."
            ),
            google_search=search,
        )
        try:
            async with websockets.connect(
                uri, open_timeout=20.0, ping_interval=None, ping_timeout=None, max_size=None
            ) as socket:
                await socket.send(json.dumps(setup))
                first = json.loads(await asyncio.wait_for(socket.recv(), timeout=25.0))
                if not ({"setupComplete", "setup_complete"} & set(first)):
                    setup_refusal = json.dumps(first)[:300]
                    _is_quota(setup_refusal)
                    if search and report.quota:
                        # Grounding and the Live allowance refuse identically, so
                        # the search tool is dropped once and the refusal
                        # re-read - the second answer is the real one.
                        report.grounding = (
                            "Google Search grounding was refused for this key (it needs "
                            "grounding quota, usually a paid plan); retrying without it"
                        )
                        report.quota = False
                        log.warn(report.grounding)
                        continue

                    # The refusal stands: with no search tool offered, a quota
                    # sentence can only mean the Live allowance itself.
                    report.handshake = "refused"
                    report.error = setup_refusal
                    return

                report.model = model
                report.voice = str(live.voice_name or gemini_live.DEFAULT_VOICE)
                report.handshake = "setupComplete"
                log.ok(f"handshake accepted ({model})")
                if report.grounding:
                    log.info(report.grounding)

                # Stream the phrase exactly as the page streams a microphone:
                # 16 kHz mono PCM, real time, in 250 ms chunks.
                log.info(
                    f"streaming {len(audio) / 2 / gemini_live.PCM_IN_RATE:.1f}s of "
                    f"audio, then waiting up to {ANSWER_TIMEOUT:.0f}s for an answer"
                )
                size = int(gemini_live.PCM_IN_RATE * CHUNK_MS / 1000) * 2
                for start in range(0, len(audio), size):
                    chunk = audio[start:start + size]
                    await socket.send(json.dumps({
                        "realtimeInput": {
                            "audio": {
                                "mimeType": f"audio/pcm;rate={gemini_live.PCM_IN_RATE}",
                                "data": base64.b64encode(chunk).decode("ascii"),
                            }
                        }
                    }))
                    await asyncio.sleep(CHUNK_MS / 1000)
                # End the turn explicitly rather than waiting for silence to be
                # inferred, so "no answer" means no answer and not "still listening".
                await socket.send(json.dumps({"realtimeInput": {"audioStreamEnd": True}}))

                deadline = asyncio.get_event_loop().time() + ANSWER_TIMEOUT
                while asyncio.get_event_loop().time() < deadline:
                    remaining = deadline - asyncio.get_event_loop().time()
                    try:
                        raw = await asyncio.wait_for(socket.recv(), timeout=max(1.0, remaining))
                    except asyncio.TimeoutError:
                        break
                    # The server sends some of these as *binary* frames on this
                    # endpoint, carrying the same JSON - ``recv()`` hands those
                    # back as ``bytes``. Treating bytes as junk silently discards
                    # real answers (a transcript, a turn, the audio) and reports
                    # a working session as a dead one, which is what this check
                    # exists to tell apart, so both types are decoded.
                    try:
                        message = json.loads(raw)
                    except Exception:
                        continue
                    _describe_wire(message, report)
                    if report.answered:
                        return
                # The session opened, took every frame that was streamed to it,
                # and said nothing: no transcript, no turn, no error. The model
                # is ignoring audio input (see gemini_live._AUDIO_DEAF), which
                # from the browser is indistinguishable from a quiet user.
                report.ignored_audio.append(model)
                gemini_live.mark_audio_deaf(
                    model,
                    "accepted the live session and ignored audio input",
                    key=key,
                )
                log.warn(f"{model} accepted the session and ignored the audio")
                continue
        except Exception as exc:
            report.handshake = report.handshake or "failed"
            report.error = f"{type(exc).__name__}: {exc}"
            # The socket closing with the quota sentence is the common shape of a
            # spent allowance: the handshake is answered and then torn down.
            if search and _is_quota(report.error):
                report.error = ""
                continue
            _is_quota(report.error)
            return


def verify_live_voice(cfg: Any, audio_path: str | None = None) -> LiveCheckReport:
    """Run the real-session check and return what happened."""
    report = LiveCheckReport()
    live = cfg.live_voice
    report.engine = readiness.resolve_provider(cfg)
    # The model this will actually open, which is not the configured one once a
    # model has been measured ignoring audio for this key.
    report.model = gemini_live.preferred_model(live, readiness.gemini_api_key(cfg))
    report.voice = str(live.voice_name or gemini_live.DEFAULT_VOICE)

    try:
        audio, source = _probe_audio(audio_path)
    except Exception as exc:
        report.error = str(exc)
        return report

    seconds = len(audio) / 2 / gemini_live.PCM_IN_RATE
    log.info(
        f"opening a real Live session on {report.model} (voice {report.voice}); "
        f"sending {seconds:.1f}s of {source}"
    )
    asyncio.run(_session(cfg, audio, report))

    if report.answered:
        # One model ignoring the audio while another answers the *same* clip is
        # proof rather than suspicion, so the verdict is written for good: the
        # next session - the browser, the terminal - opens on a model that
        # hears instead of spending the watchdog window to relearn this.
        key = readiness.gemini_api_key(cfg)
        for name in report.ignored_audio:
            gemini_live.mark_audio_deaf(
                name,
                "accepted the live session and ignored audio input",
                key=key,
                proven=True,
            )
        if report.ignored_audio:
            log.info(
                "remembered: " + ", ".join(report.ignored_audio)
                + " will not be tried first again for this key (set "
                + gemini_live._AUDIO_STATE_ENV + " to relocate, delete the file to reset)"
            )

    if report.heard:
        log.ok(f"the model transcribed your audio: {report.input_transcript[:120]!r}")
    elif report.answered:
        log.warn("the model answered but did not transcribe the audio it was sent")
    elif report.ignored_audio:
        log.warn(
            "; ".join(report.ignored_audio)
            + " accepted the live session and ignored everything sent to it, and no "
            "other live model answered either"
        )
    return report
