"""Voice I/O: speak replies (TTS) and hear commands (STT).

TTS  - local Kokoro-82M (ONNX, offline, ~5x real-time on CPU) when its model
       files are in models/tts/; Gemini TTS on Vertex AI as fallback.
STT  - record the microphone with sounddevice (simple energy-based
       start/stop), then transcribe with the existing Gemini backend, which
       accepts audio natively - no local Whisper/torch on this machine.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
import io
import math
import os
import struct
import tempfile
import threading
import wave

from ..config import VoiceConfig
from . import logging as log

_RATE = 16000          # 16 kHz mono int16 - plenty for speech
_CHUNK = 1600          # 0.1 s per energy reading


# --------------------------------------------------------------------------- #
# Gemini TTS
# --------------------------------------------------------------------------- #

_brain = None
_tts_config = VoiceConfig()
_async_wav_path: str | None = None
_speech_generation = 0
_voice_epoch = 0
_speech_state = threading.Condition()
_speech_lifecycle_lock = threading.Lock()
_speech_playback_lock = threading.Lock()
_sync_speech_lock = threading.Lock()
_sync_owner: object | None = None


@dataclass(frozen=True)
class _VoiceSnapshot:
    """Immutable configuration captured when an utterance is submitted."""

    brain: object
    config: VoiceConfig
    epoch: int


class _LatestSpeechDispatcher:
    """Two bounded daemon workers with one coalescing pending slot.

    One stalled cloud request cannot block the next reply. If both requests
    stall, repeated responses replace the one pending job instead of growing
    threads, HTTP sessions, memory, and quota usage without bound.
    """

    def __init__(self, workers: int = 2):
        self._worker_count = max(1, workers)
        self._condition = threading.Condition()
        self._pending: tuple | None = None
        self._workers: list[threading.Thread] = []

    def submit(
        self,
        text: str,
        generation: int,
        snapshot: _VoiceSnapshot,
    ) -> None:
        with self._condition:
            self._pending = (text, generation, snapshot)
            if not self._workers:
                for index in range(self._worker_count):
                    worker = threading.Thread(
                        target=self._run,
                        daemon=True,
                        name=f"jarvis-tts-{index + 1}",
                    )
                    self._workers.append(worker)
                    worker.start()
            self._condition.notify_all()

    def discard_pending(self) -> None:
        with self._condition:
            self._pending = None

    def _run(self) -> None:
        while True:
            with self._condition:
                while self._pending is None:
                    self._condition.wait()
                job = self._pending
                self._pending = None
            _speak_async(*job)


_SPEECH_DISPATCHER = _LatestSpeechDispatcher()


def _next_speech_generation_locked() -> int:
    global _speech_generation
    _speech_generation += 1
    return _speech_generation


def _voice_snapshot_locked() -> _VoiceSnapshot:
    return _VoiceSnapshot(
        brain=_brain,
        config=copy.copy(_tts_config),
        epoch=_voice_epoch,
    )


def _next_speech_generation() -> int:
    with _speech_state:
        return _next_speech_generation_locked()


def _speech_is_current(
    generation: int,
    snapshot: _VoiceSnapshot | None = None,
) -> bool:
    with _speech_state:
        return (
            generation == _speech_generation
            and (snapshot is None or snapshot.epoch == _voice_epoch)
        )


def _current_voice_snapshot() -> _VoiceSnapshot:
    with _speech_state:
        return _voice_snapshot_locked()


def configure(brain, config: VoiceConfig) -> None:
    """Connect voice output to Jarvis's authenticated Gemini brain."""
    global _brain, _tts_config, _voice_epoch, _sync_owner, _kokoro_broken
    with _speech_lifecycle_lock:
        with _speech_state:
            _next_speech_generation_locked()
            _voice_epoch += 1
            _brain = brain
            _tts_config = copy.copy(config)
            _kokoro_broken = False
            _sync_owner = None
            _SPEECH_DISPATCHER.discard_pending()
            _speech_state.notify_all()
        _stop_async_playback()


def reset() -> None:
    """Clear configured TTS state and stop any asynchronous playback."""
    global _brain, _tts_config, _voice_epoch, _sync_owner, _kokoro_broken
    # Invalidate synthesis immediately; never wait behind a cloud request.
    with _speech_lifecycle_lock:
        with _speech_state:
            _next_speech_generation_locked()
            _voice_epoch += 1
            _brain = None
            _tts_config = VoiceConfig()
            _kokoro_broken = False  # the loaded Kokoro model itself is kept
            _sync_owner = None
            _SPEECH_DISPATCHER.discard_pending()
            _speech_state.notify_all()
        _stop_async_playback()


def _synthesize(
    text: str,
    snapshot: _VoiceSnapshot | None = None,
) -> bytes:
    snapshot = snapshot or _current_voice_snapshot()
    brain = snapshot.brain
    config = snapshot.config
    if brain is None:
        raise RuntimeError("Gemini TTS is not configured")
    synthesize = getattr(brain, "synthesize_speech", None)
    if synthesize is None:
        raise RuntimeError(
            "the active brain cannot authenticate Gemini TTS; "
            "use the gemini/Vertex backend"
        )
    return synthesize(
        text,
        model=config.model,
        voice_name=config.voice,
        language_code=config.language_code,
    )


def _wav_bytes(pcm: bytes, rate: int = 24000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(rate)
        wav_file.writeframes(pcm)
    return output.getvalue()


# --------------------------------------------------------------------------- #
# Local TTS (Kokoro-82M via kokoro-onnx)
# --------------------------------------------------------------------------- #

_kokoro = None                 # loaded once, on first utterance
_kokoro_broken = False         # failed once -> stop retrying, use Gemini
_kokoro_lock = threading.Lock()


def _synthesize_kokoro(text: str, config: VoiceConfig) -> bytes:
    """WAV bytes from the local Kokoro model (raises if unavailable)."""
    global _kokoro
    # Kokoro's model initialization and create() are not documented as
    # thread-safe. Serialize only this fast local engine; cloud calls retain
    # the dispatcher's two-way concurrency.
    with _kokoro_lock:
        if _kokoro is None:
            from ..config import ROOT
            model_dir = ROOT / "models" / "tts"
            from kokoro_onnx import Kokoro
            _kokoro = Kokoro(str(model_dir / "kokoro-v1.0.onnx"),
                             str(model_dir / "voices-v1.0.bin"))
        samples, rate = _kokoro.create(
            text,
            voice=config.local_voice,
            speed=config.local_speed,
        )
    import numpy as np
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
    return _wav_bytes(pcm, rate)


class _SupersededSpeech(Exception):
    pass


def _synthesize_wav(
    text: str,
    snapshot: _VoiceSnapshot | None = None,
    generation: int | None = None,
) -> bytes:
    """WAV bytes from the configured engine: local Kokoro first, Gemini after."""
    global _kokoro_broken
    snapshot = snapshot or _current_voice_snapshot()
    config = snapshot.config
    with _speech_state:
        current_epoch = snapshot.epoch == _voice_epoch
        kokoro_broken = _kokoro_broken if current_epoch else False
    if config.engine == "kokoro" and not kokoro_broken:
        try:
            return _synthesize_kokoro(text, config)
        except Exception as exc:
            with _speech_state:
                still_current = (
                    snapshot.epoch == _voice_epoch
                    and (
                        generation is None
                        or generation == _speech_generation
                    )
                )
                if still_current:
                    _kokoro_broken = True  # warn once, not every sentence
            if not still_current:
                raise _SupersededSpeech() from exc
            log.warn(f"local TTS unavailable ({exc}); using Gemini TTS")
    with _speech_state:
        if (
            snapshot.epoch != _voice_epoch
            or (
                generation is not None
                and generation != _speech_generation
            )
        ):
            raise _SupersededSpeech()
    return _wav_bytes(_synthesize(text, snapshot))


def _stop_async_playback() -> None:
    global _async_wav_path
    try:
        import winsound
        winsound.PlaySound(None, 0)
    except Exception:
        pass
    if _async_wav_path:
        try:
            os.unlink(_async_wav_path)
        except OSError:
            pass
        _async_wav_path = None


def _play_wav(data: bytes, wait: bool) -> None:
    """Play WAV bytes on Windows while preserving sync/async voice behavior."""
    import winsound

    global _async_wav_path
    _stop_async_playback()
    if wait:
        winsound.PlaySound(
            data,
            winsound.SND_MEMORY | winsound.SND_NODEFAULT,
        )
        return

    # Windows cannot reliably combine SND_MEMORY and SND_ASYNC. Keep a
    # temporary WAV alive until the next utterance replaces it.
    handle, path = tempfile.mkstemp(prefix="jarvis-tts-", suffix=".wav")
    try:
        with os.fdopen(handle, "wb") as output:
            output.write(data)
        _async_wav_path = path
        winsound.PlaySound(
            path,
            winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
        )
    except Exception:
        try:
            os.close(handle)
        except OSError:
            pass
        try:
            os.unlink(path)
        except OSError:
            pass
        _async_wav_path = None
        raise


def _speak_async(
    text: str,
    generation: int,
    snapshot: _VoiceSnapshot,
) -> None:
    """Synthesize in a bounded daemon worker and play only the latest reply."""
    if not _speech_is_current(generation, snapshot):
        return
    try:
        data = _synthesize_wav(
            text,
            snapshot=snapshot,
            generation=generation,
        )
        # A synchronous goodbye/microphone hand-off has playback priority.
        # Wait without occupying the playback lock, then recheck under both
        # locks so reset/new speech cannot race stale audio into existence.
        while True:
            with _speech_state:
                while (
                    _sync_owner is not None
                    and generation == _speech_generation
                    and snapshot.epoch == _voice_epoch
                ):
                    _speech_state.wait()
                if (
                    generation != _speech_generation
                    or snapshot.epoch != _voice_epoch
                ):
                    return
            with _speech_lifecycle_lock:
                with _speech_playback_lock:
                    with _speech_state:
                        if (
                            generation != _speech_generation
                            or snapshot.epoch != _voice_epoch
                        ):
                            return
                        if _sync_owner is not None:
                            continue
                        # Lifecycle + state remain held through quick async
                        # PlaySound initiation. Reset either wins before this
                        # check or stops the playback after it starts.
                        _play_wav(data, False)
                        return
    except _SupersededSpeech:
        return
    except Exception as exc:
        # Superseded cloud calls may finish with an error much later. Do not
        # print stale warnings into an otherwise idle prompt/browser session.
        if _speech_is_current(generation, snapshot):
            log.warn(f"TTS unavailable: {exc}")


def _speak_sync(text: str) -> None:
    """Synthesize and play a blocking utterance without supersession."""
    global _sync_owner
    with _sync_speech_lock:
        owner = object()
        with _speech_state:
            _next_speech_generation_locked()
            snapshot = _voice_snapshot_locked()
            _sync_owner = owner
            _speech_state.notify_all()
        try:
            # Synthesis deliberately happens outside the playback/state locks:
            # reset and reconfiguration remain immediate even if the cloud
            # request stalls for its entire timeout.
            data = _synthesize_wav(text, snapshot=snapshot)
            with _speech_lifecycle_lock:
                with _speech_playback_lock:
                    with _speech_state:
                        if (
                            snapshot.epoch != _voice_epoch
                            or _sync_owner is not owner
                        ):
                            return
                    # PlaySound's synchronous API combines initiation and wait,
                    # so retain the lifecycle gate for the audio duration. A
                    # reset during synthesis is still immediate; a reset after
                    # playback begins waits for this promised sync utterance.
                    _play_wav(data, True)
        except _SupersededSpeech:
            return
        except Exception as exc:
            with _speech_state:
                current = snapshot.epoch == _voice_epoch
            if current:
                log.warn(f"TTS unavailable: {exc}")
        finally:
            with _speech_state:
                if _sync_owner is owner:
                    _sync_owner = None
                _speech_state.notify_all()


def speak(text: str, wait: bool = False) -> None:
    """Say text out loud without blocking typed/browser replies.

    Non-waiting speech uses two bounded daemon workers and coalesces excess
    replies, so one slow request cannot hold up the next response or process
    exit. ``wait=True`` remains fully synchronous for microphone and farewell
    call sites.
    """
    text = (text or "").strip()
    if not text:
        return
    if wait:
        try:
            _speak_sync(text)
        except BaseException:
            # A Ctrl+C during a blocking farewell/microphone hand-off also
            # cancels any newer background synthesis before the caller exits.
            with _speech_lifecycle_lock:
                with _speech_state:
                    _next_speech_generation_locked()
                    _speech_state.notify_all()
                _SPEECH_DISPATCHER.discard_pending()
                _stop_async_playback()
            raise
        return
    with _speech_state:
        generation = _next_speech_generation_locked()
        snapshot = _voice_snapshot_locked()
        _SPEECH_DISPATCHER.submit(text, generation, snapshot)


def speak_to_wav(text: str, path: str) -> bool:
    """Render speech to a WAV file (used by the self-test; no speakers needed)."""
    try:
        snapshot = _current_voice_snapshot()
        with open(path, "wb") as output:
            output.write(_synthesize_wav(text, snapshot=snapshot))
        return True
    except Exception as exc:
        log.warn(f"TTS-to-file failed: {exc}")
        return False


# --------------------------------------------------------------------------- #
# Wake word
# --------------------------------------------------------------------------- #

def wait_for_wake(phrase: str = "hey jarvis", timeout: float | None = None,
                  _wav_file: str | None = None) -> bool:
    """Block until the wake phrase is spoken. Offline, near-zero CPU.

    Uses Windows' built-in SAPI recognizer with a one-phrase grammar - no
    audio ever leaves the machine while waiting. Returns True on wake,
    False on timeout or if the recognizer is unavailable. Ctrl+C propagates
    so the caller can exit hands-free mode.

    ``_wav_file`` feeds a file instead of the microphone (self-tests).
    """
    import time as _t
    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
    except Exception as exc:
        log.warn(f"wake word needs pywin32: {exc}")
        return False

    pythoncom.CoInitialize()
    grammar = None
    try:
        rec = win32com.client.Dispatch("SAPI.SpInprocRecognizer")
        if _wav_file:
            stream = win32com.client.Dispatch("SAPI.SpFileStream")
            stream.Open(_wav_file)
            rec.AudioInputStream = stream
        else:
            rec.AudioInput = win32com.client.Dispatch("SAPI.SpMMAudioIn")
        ctx = rec.CreateRecoContext()
        grammar = ctx.CreateGrammar()
        rule = grammar.Rules.Add("wake", 1 | 2)      # SRATopLevel|SRADynamic
        rule.InitialState.AddWordTransition(None, phrase)
        grammar.Rules.Commit()
        grammar.CmdSetRuleState("wake", 1)           # SGDSActive

        heard: list[int] = []

        class _Sink:
            def OnRecognition(self, *args):
                heard.append(1)

        win32com.client.WithEvents(ctx, _Sink)
        t0 = _t.time()
        while not heard:
            pythoncom.PumpWaitingMessages()
            _t.sleep(0.05)
            if timeout is not None and _t.time() - t0 > timeout:
                return False
        return True
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        log.warn(f"wake-word listener failed: {exc}")
        return False
    finally:
        try:
            if grammar is not None:
                grammar.CmdSetRuleState("wake", 0)   # release before mic reuse
        except Exception:
            pass
        pythoncom.CoUninitialize()


# --------------------------------------------------------------------------- #
# STT
# --------------------------------------------------------------------------- #

class _EnergyVAD:
    """Streaming energy VAD tuned to ignore steady background (e.g. fans).

    Feed it one RMS level per audio chunk (``_CHUNK`` = 0.1 s).

    START: the fan/room level is tracked continuously as the noise floor (an
    EWMA that only learns from below-threshold chunks, so speech never inflates
    it). Recording begins only when a chunk is ``START_FACTOR`` * above that
    floor AND stays there for ``START_HOLD`` chunks - a fan surge or a thud is
    brief and never sustains, so it can't trip recording; a spoken word does.

    STOP: end-of-speech is judged relative to the speech's own loudness
    (``speech_ref``), not the floor, so it can't get wedged when the background
    sits higher than it did at warmup - the failure that made recording run to
    ``max_seconds`` every time.
    """
    START_FACTOR = 3.0     # speech must exceed the noise floor by this ratio
    START_HOLD = 3         # ... for this many chunks (~0.3 s) - rejects surges
    STOP_FRAC = 0.35       # end when level falls below this * speech loudness
    SPEECH_DECAY = 0.97    # speech_ref bleeds down so one loud blip can't stick
    MIN_FLOOR = 120.0      # a near-zero floor (muted mic) can't drive the gate
    WARMUP = 5             # ~0.5 s to let the floor settle on the fan noise

    def __init__(self, silence_after: float, chunk: float = 0.1):
        self.silence_after = silence_after
        self.chunk = chunk
        self.floor: float | None = None
        self.speech_ref = 0.0
        self.started = False
        self.quiet = 0.0
        self.warmed = 0
        self.hold = 0

    def _track_floor(self, level: float) -> None:
        self.floor = level if self.floor is None else 0.9 * self.floor + 0.1 * level

    def feed(self, level: float) -> str:
        """Return 'listening' (pre-speech), 'recording', or 'stop'."""
        if not self.started:
            floor = max(self.MIN_FLOOR,
                        self.floor if self.floor is not None else level)
            if self.warmed < self.WARMUP:         # settle the floor on the fans
                self.warmed += 1                  # before the start gate arms
                self._track_floor(level)
                return "listening"
            if level >= self.START_FACTOR * floor:
                self.hold += 1                    # loud - but is it sustained?
                if self.hold >= self.START_HOLD:
                    self.started = True
                    self.speech_ref = level
                    return "recording"
                return "listening"
            self.hold = 0                         # dropped back to the fans
            self._track_floor(level)              # learn the floor from them only
            return "listening"
        # Recording: track the speech's loudness (jump up fast, bleed down slow),
        # and call it silence once the level drops well below that.
        self.speech_ref = max(level, self.SPEECH_DECAY * self.speech_ref)
        if level < self.STOP_FRAC * self.speech_ref:
            self.quiet += self.chunk
        else:
            self.quiet = 0.0
        return "stop" if self.quiet >= self.silence_after else "recording"


def listen(start_timeout: float = 6.0, max_seconds: float = 12.0,
           silence_after: float | None = None) -> bytes | None:
    """Record one spoken phrase from the default microphone.

    Waits up to ``start_timeout`` for speech to begin, then records until
    ``silence_after`` seconds of quiet (or ``max_seconds`` total). Returns
    WAV bytes, or None if nothing was heard / no microphone.
    """
    try:
        import sounddevice as sd  # type: ignore
    except Exception as exc:
        log.warn(f"microphone unavailable: {exc}")
        return None
    if silence_after is None:
        silence_after = _tts_config.silence_after

    def rms(chunk: bytes) -> float:
        n = len(chunk) // 2
        if not n:
            return 0.0
        samples = struct.unpack(f"<{n}h", chunk)
        return math.sqrt(sum(s * s for s in samples) / n)

    from collections import deque
    frames: list[bytes] = []
    waited = 0.0
    det = _EnergyVAD(silence_after)
    # Keep the last few pre-speech chunks so the start-hold delay doesn't clip
    # the onset of the first word.
    preroll: deque = deque(maxlen=_EnergyVAD.START_HOLD)
    try:
        with sd.RawInputStream(samplerate=_RATE, channels=1, dtype="int16",
                               blocksize=_CHUNK) as stream:
            while True:
                chunk, _ = stream.read(_CHUNK)
                chunk = bytes(chunk)
                state = det.feed(rms(chunk))
                if state == "listening":
                    preroll.append(chunk)
                    waited += 0.1
                    if waited >= start_timeout:
                        return None               # heard nothing
                    continue
                if not frames:                    # just started - recover onset
                    frames.extend(preroll)
                frames.append(chunk)
                if state == "stop" or len(frames) * 0.1 >= max_seconds:
                    hit_cap = state != "stop"
                    break
    except Exception as exc:
        log.warn(f"recording failed: {exc}")
        return None

    secs = len(frames) * 0.1
    log.info(f"recorded {secs:.1f}s"
             + (" (hit max_seconds - silence never detected)" if hit_cap else ""))

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(_RATE)
        wf.writeframes(b"".join(frames))
    return buf.getvalue()


def transcribe(wav_bytes: bytes, brain) -> str:
    """Turn recorded speech into text using the configured brain backend."""
    fn = getattr(brain, "transcribe_audio", None)
    if fn is None:
        log.warn("this brain backend cannot transcribe audio "
                 "(voice input needs the gemini backend).")
        return ""
    try:
        return (fn(
            wav_bytes,
            model=_tts_config.transcription_model,
        ) or "").strip()
    except Exception as exc:
        log.warn(f"transcription failed: {exc}")
        return ""
