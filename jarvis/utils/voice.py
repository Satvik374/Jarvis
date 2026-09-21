"""Voice I/O: speak replies (TTS) and hear commands (STT).

TTS  - local Kokoro-82M (ONNX, offline, ~5x real-time on CPU) when its model
       files are in models/tts/; Gemini TTS on Vertex AI as fallback.
STT  - record the microphone with sounddevice (simple energy-based
       start/stop), then transcribe with the existing Gemini backend, which
       accepts audio natively - no local Whisper/torch on this machine.
"""

from __future__ import annotations

import atexit
import copy
from dataclasses import dataclass
import io
import math
import os
import queue
import struct
import tempfile
import threading
import time
import wave

from ..config import VoiceConfig
from . import logging as log
from .paths import live_flag_path

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
_active_playback_lock = threading.Lock()
_sync_speech_lock = threading.Lock()
_sync_owner: object | None = None
_active_playback_cancel: threading.Event | None = None
from pathlib import Path

_is_speaking_flag = False
_live_mode_active = False

#: Live mode is signalled *across processes* - the browser server owns the flag
#: and the console worker reads it - through a file in the temp directory. The
#: reader used to trust the file's mere existence, so a live session that was
#: crashed or force-killed left the flag behind and muted every later session
#: forever: not only live voice, but ordinary terminal speech too. The owner now
#: heartbeats the file while live mode is on, and a flag that has gone quiet is
#: treated as abandoned and removed. Where the flag lives is decided by
#: ``paths.live_flag_path``; this module only owns its heartbeat and lifetime.
_LIVE_FLAG_HEARTBEAT_SECONDS = 15.0
_LIVE_FLAG_MAX_AGE_SECONDS = 90.0
_live_flag_owner_pid: int | None = None
_live_flag_heartbeat: threading.Thread | None = None
_live_flag_stop = threading.Event()


def _clear_live_flag(force: bool = False) -> None:
    """Stop heartbeating and remove the flag.

    ``force`` is for an explicit stop, which ends live mode for the machine
    whoever raised the flag. Without it the removal is ownership-scoped, which
    is what an exit path needs: a process must not delete a flag another live
    session is still heartbeating.
    """
    global _live_flag_owner_pid, _live_flag_heartbeat
    _live_flag_stop.set()
    _live_flag_heartbeat = None
    owned = _live_flag_owner_pid == os.getpid()
    _live_flag_owner_pid = None
    if not (force or owned):
        return
    try:
        path = live_flag_path()
        if path.exists():
            path.unlink()
    except Exception:
        pass


def _release_live_flag_at_exit() -> None:
    """Never leave a live-mode flag for the next session to inherit."""
    if _live_flag_owner_pid == os.getpid():
        _clear_live_flag()


atexit.register(_release_live_flag_at_exit)


def _heartbeat_live_flag() -> None:
    """Keep the flag fresh so other processes can keep trusting it."""
    while not _live_flag_stop.wait(_LIVE_FLAG_HEARTBEAT_SECONDS):
        try:
            path = live_flag_path()
            if path.exists():
                os.utime(path, None)
            else:
                path.write_text(str(os.getpid()), encoding="utf-8")
        except Exception:
            return


def set_live_mode_active(active: bool) -> None:
    """Set whether Live Voice Mode (e.g. gpt-realtime) is active.
    
    When active, the Communication Agent is silenced and must not speak.
    """
    global _live_mode_active, _live_flag_owner_pid, _live_flag_heartbeat
    _live_mode_active = bool(active)
    if not _live_mode_active:
        _clear_live_flag(force=True)
        return
    _live_flag_stop.clear()
    _live_flag_owner_pid = os.getpid()
    try:
        live_flag_path().write_text(str(os.getpid()), encoding="utf-8")
    except Exception:
        pass
    if _live_flag_heartbeat is None or not _live_flag_heartbeat.is_alive():
        _live_flag_heartbeat = threading.Thread(
            target=_heartbeat_live_flag,
            name="jarvis-live-flag-heartbeat",
            daemon=True,
        )
        _live_flag_heartbeat.start()
    interrupt_speech()


def is_live_mode_active() -> bool:
    """Return True if Live Voice Mode is active (Communication Agent must not speak).

    A flag whose owner stopped heartbeating it is abandoned: a crashed or
    force-killed live session must not leave Jarvis permanently mute, so the
    flag is discarded and reported as inactive.
    """
    if _live_mode_active or os.environ.get("JARVIS_LIVE_MODE") == "1":
        return True
    try:
        age = time.time() - live_flag_path().stat().st_mtime
    except OSError:
        return False
    except Exception:
        return False
    if age <= _LIVE_FLAG_MAX_AGE_SECONDS:
        return True
    try:
        live_flag_path().unlink()
    except Exception:
        pass
    return False


def is_speaking() -> bool:
    """Return True if Jarvis is currently playing audio or synthesizing speech."""
    with _speech_state:
        return _is_speaking_flag or (_active_playback_cancel is not None and not _active_playback_cancel.is_set())


def interrupt_speech() -> bool:
    """Instantly silence active speech playback and cancel pending synthesis (Barge-in)."""
    global _speech_generation, _active_playback_cancel, _is_speaking_flag
    was_speaking = False
    with _speech_lifecycle_lock:
        with _speech_state:
            _speech_generation += 1
            if _active_playback_cancel and not _active_playback_cancel.is_set():
                _active_playback_cancel.set()
                was_speaking = True
            _is_speaking_flag = False
            _SPEECH_DISPATCHER.discard_pending()
            _speech_state.notify_all()
        _stop_async_playback()
    return was_speaking



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
    global _brain, _tts_config, _voice_epoch, _sync_owner, _kokoro_broken, _gemini_broken, _edge_broken, _azure_speech_broken, _fish_audio_broken
    with _speech_lifecycle_lock:
        with _speech_state:
            _next_speech_generation_locked()
            _voice_epoch += 1
            _brain = brain
            _tts_config = copy.copy(config)
            _kokoro_broken = False
            _gemini_broken = False
            _edge_broken = False
            _azure_speech_broken = False
            _fish_audio_broken = False
            _sync_owner = None
            _SPEECH_DISPATCHER.discard_pending()
            _speech_state.notify_all()
        _stop_async_playback()


def reset() -> None:
    """Clear configured TTS state and stop any asynchronous playback."""
    global _brain, _tts_config, _voice_epoch, _sync_owner, _kokoro_broken, _gemini_broken, _edge_broken, _azure_speech_broken, _fish_audio_broken
    # Invalidate synthesis immediately; never wait behind a cloud request.
    with _speech_lifecycle_lock:
        with _speech_state:
            _next_speech_generation_locked()
            _voice_epoch += 1
            _brain = None
            _tts_config = VoiceConfig()
            _kokoro_broken = False  # the loaded Kokoro model itself is kept
            _gemini_broken = False
            _edge_broken = False
            _azure_speech_broken = False
            _fish_audio_broken = False
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
_kokoro_broken = False         # failed once -> stop retrying, use Edge/Gemini
_gemini_broken = False         # failed once -> stop retrying, use Edge
_edge_broken = False           # failed once -> stop retrying, use SAPI
_azure_speech_broken = False   # failed once -> stop retrying, use Kokoro/Edge/SAPI
_fish_audio_broken = False     # failed once -> stop retrying, use Edge/Kokoro/SAPI
_kokoro_lock = threading.Lock()


def _clean_for_speech(text: str) -> str:
    """Prepare assistant response for natural text-to-speech synthesis.

    Strips terminal/agent prefixes, emojis, code blocks, URLs, markdown syntax,
    and excessive length so local TTS reads cleanly and naturally.
    """
    if not text:
        return ""

    import re

    # 1. Remove agent labels and prefixes:
    # "🎙️ [Communicating Agent]: ...", "[Side Agent]: ...", "[Worker Question]: ...", etc.
    cleaned = re.sub(
        r"^(?:[\U00010000-\U0010ffff\u2600-\u27bf\ufe00-\ufe0f\s]*\[(?:Communicating Agent|Side Agent|Worker Question|scheduled|HUD)\][:\s]*)",
        "",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )

    # 2. Replace multi-line code blocks with brief spoken indicator
    cleaned = re.sub(r"```[\w]*\n[\s\S]*?\n```", " [Code snippet provided] ", cleaned)
    # Remove inline backticks: `code` -> code
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)

    # 3. Replace Markdown links [text](url) -> text
    cleaned = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", cleaned)

    # 4. Replace raw URLs (http:// or https://) with simplified text
    cleaned = re.sub(r"https?://(?:www\.)?(\S+)", r"the link", cleaned)

    # 5. Remove markdown headers (#, ##, ###)
    cleaned = re.sub(r"^\s*#{1,6}\s*", "", cleaned, flags=re.MULTILINE)

    # 6. Remove markdown formatting markers (*, **, _, __, ~~, >, |)
    cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"\*([^*]+)\*", r"\1", cleaned)
    cleaned = re.sub(r"__([^_]+)__", r"\1", cleaned)
    cleaned = re.sub(r"_([^_]+)_", r"\1", cleaned)
    cleaned = re.sub(r"~~([^~]+)~~", r"\1", cleaned)
    cleaned = re.sub(r"^\s*>\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^\s*[-*+•]\s+", "", cleaned, flags=re.MULTILINE)

    # 7. Strip emojis and unicode symbols that phonemizers mispronounce
    cleaned = re.sub(r"[\U00010000-\U0010ffff\u2600-\u27bf\ufe00-\ufe0f]", "", cleaned)

    # 8. Collapse whitespace and repeated punctuation
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n\s*\n+", ". ", cleaned)
    cleaned = re.sub(r"\n", " ", cleaned)
    cleaned = re.sub(r"\.{2,}", ".", cleaned)
    cleaned = cleaned.strip()

    # 9. Cap length for spoken audio if message is overwhelmingly long (e.g. > 900 chars)
    # to avoid holding the audio channel for minutes when a large code/text payload is generated
    if len(cleaned) > 900:
        cutoff = cleaned[:850].rfind(". ")
        if cutoff > 400:
            cleaned = cleaned[:cutoff + 1] + " More details are shown above."
        else:
            cleaned = cleaned[:800].rsplit(" ", 1)[0] + "... More details are shown above."

    return cleaned


def _synthesize_kokoro(text: str, config: VoiceConfig) -> bytes:
    """WAV bytes from the local Kokoro model (raises if unavailable)."""
    global _kokoro
    with _kokoro_lock:
        if _kokoro is None:
            from ..config import ROOT
            model_dir = ROOT / "models" / "tts"
            from kokoro_onnx import Kokoro
            _kokoro = Kokoro(str(model_dir / "kokoro-v1.0.onnx"),
                             str(model_dir / "voices-v1.0.bin"))
        voice_name = getattr(config, "local_voice", "bm_george") or "bm_george"
        try:
            speed = float(getattr(config, "local_speed", 1.0) or 1.0)
        except (ValueError, TypeError):
            speed = 1.0
        samples, rate = _kokoro.create(
            text,
            voice=voice_name,
            speed=speed,
        )
    import numpy as np
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
    return _wav_bytes(pcm, rate)


def _synthesize_edge_tts(text: str, config: VoiceConfig) -> bytes:
    """WAV bytes from Microsoft Edge Neural TTS (fast, natural, free)."""
    import asyncio
    import edge_tts
    import soundfile as sf

    voice = "en-GB-RyanNeural"  # Default classic Jarvis British voice
    voice_lower = (config.voice or "").lower()
    local_voice_lower = (getattr(config, "local_voice", "") or "").lower()
    lang = (config.language_code or "en-US").lower()

    if "us" in lang or "guy" in voice_lower or "puck" in voice_lower:
        voice = "en-US-GuyNeural"
    elif "female" in voice_lower or "aria" in voice_lower or "kore" in voice_lower or "heart" in local_voice_lower:
        voice = "en-US-AriaNeural"
    elif "gb" in lang or "uk" in lang or "british" in voice_lower or "george" in local_voice_lower or "lewis" in local_voice_lower:
        voice = "en-GB-RyanNeural"

    async def _fetch():
        communicate = edge_tts.Communicate(text, voice)
        mp3_buf = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                mp3_buf.write(chunk.get("data", b""))
        return mp3_buf.getvalue()

    try:
        mp3_data = asyncio.run(_fetch())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            mp3_data = loop.run_until_complete(_fetch())
        finally:
            loop.close()

    if not mp3_data:
        raise RuntimeError("Edge-TTS returned empty audio stream")

    data, samplerate = sf.read(io.BytesIO(mp3_data))
    wav_out = io.BytesIO()
    sf.write(wav_out, data, samplerate, format="WAV", subtype="PCM_16")
    return wav_out.getvalue()


def _synthesize_sapi(text: str, config: VoiceConfig) -> bytes:
    """100% offline Windows SAPI5 / pyttsx3 fallback speech synthesis."""
    import tempfile
    import pyttsx3

    engine = pyttsx3.init()
    try:
        speed = float(getattr(config, "local_speed", 1.0) or 1.0)
        engine.setProperty("rate", int(180 * speed))
    except Exception:
        pass

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        tpath = f.name
    try:
        engine.save_to_file(text, tpath)
        engine.runAndWait()
        with open(tpath, "rb") as f:
            wav_data = f.read()
        if not wav_data:
            raise RuntimeError("Windows SAPI produced 0 bytes")
        return wav_data
    finally:
        if os.path.exists(tpath):
            try:
                os.unlink(tpath)
            except OSError:
                pass


def _get_azure_speech_key_dynamic(config: VoiceConfig | None = None) -> str:
    key = (
        (getattr(config, "azure_speech_key", None) if config else None)
        or os.environ.get("AZURE_SPEECH_KEY")
        or os.environ.get("SPEECH_KEY")
        or os.environ.get("AZURE_SPEECH_API_KEY")
        or os.environ.get("AZURE_API_KEY")
        or ""
    )
    if not key:
        try:
            from ..config import ROOT
            env_file = ROOT / ".env"
            if env_file.exists():
                for line in env_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    for prefix in ("AZURE_SPEECH_KEY=", "SPEECH_KEY=", "JARVIS_AZURE_SPEECH_KEY="):
                        if line.startswith(prefix):
                            val = line.split("=", 1)[1].strip().strip("'\"")
                            if val:
                                key = val
                                os.environ["AZURE_SPEECH_KEY"] = val
                                break
                    if key:
                        break
        except Exception:
            pass
    if not key:
        try:
            from ..security import get_secret
            key = get_secret("AZURE_SPEECH_KEY") or get_secret("SPEECH_KEY") or ""
        except Exception:
            pass
    return key


def _get_fish_audio_key_dynamic(config: VoiceConfig | None = None) -> str:
    """Resolve Fish Audio API key from config, environment, .env file, or credential vault."""
    key = (
        (getattr(config, "fish_audio_key", None) if config else None)
        or os.environ.get("FISH_AUDIO_API_KEY")
        or os.environ.get("FISH_API_KEY")
        or os.environ.get("JARVIS_FISH_AUDIO_API_KEY")
        or ""
    )
    if not key:
        try:
            from ..config import ROOT
            env_file = ROOT / ".env"
            if env_file.exists():
                for line in env_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    for prefix in ("FISH_AUDIO_API_KEY=", "FISH_API_KEY=", "JARVIS_FISH_AUDIO_API_KEY="):
                        if line.startswith(prefix):
                            val = line.split("=", 1)[1].strip().strip("'\"")
                            if val:
                                key = val
                                os.environ["FISH_AUDIO_API_KEY"] = val
                                break
                    if key:
                        break
        except Exception:
            pass
    if not key:
        try:
            from ..security import get_secret
            key = get_secret("FISH_AUDIO_API_KEY") or get_secret("FISH_API_KEY") or ""
        except Exception:
            pass
    return key


def _synthesize_fish_audio(text: str, config: VoiceConfig) -> bytes:
    """WAV bytes synthesized using the Fish Audio API (https://fish.audio)."""
    import json
    import urllib.request
    import urllib.error

    api_key = _get_fish_audio_key_dynamic(config)
    if not api_key:
        raise RuntimeError(
            "Fish Audio API key is required. Please set FISH_AUDIO_API_KEY in your .env, "
            "config.yaml, or store it in the Windows Credential Vault."
        )

    endpoint_url = (
        getattr(config, "fish_audio_endpoint", None)
        or os.environ.get("FISH_AUDIO_ENDPOINT")
        or "https://api.fish.audio/v1/tts"
    )
    voice_id = (
        getattr(config, "fish_audio_voice_id", None)
        or os.environ.get("FISH_AUDIO_VOICE_ID")
        or getattr(config, "fish_audio_reference_id", None)
        or os.environ.get("FISH_AUDIO_REFERENCE_ID")
        or "28b049a7574f46bc9d7122761363bda0"
    )
    model = (
        getattr(config, "fish_audio_model", None)
        or os.environ.get("FISH_AUDIO_MODEL")
        or "s2.1-pro"
    )

    latency = (
        getattr(config, "fish_audio_latency", None)
        or os.environ.get("FISH_AUDIO_LATENCY")
        or "normal"
    )

    payload: dict = {
        "text": text,
        "format": "wav",
        "latency": latency,
    }
    if voice_id:
        payload["reference_id"] = voice_id

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    if model:
        headers["model"] = model

    req = urllib.request.Request(
        endpoint_url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30.0) as resp:
            wav_bytes = resp.read()
            if not wav_bytes:
                raise RuntimeError(f"Fish Audio returned empty audio stream from {endpoint_url}")
            return wav_bytes
    except urllib.error.HTTPError as err:
        err_body = ""
        try:
            err_body = err.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        if err.code == 402 and model != "s2.1-pro-free":
            log.info("Fish Audio returned 402 (Insufficient API credit); automatically retrying with free model 's2.1-pro-free'...")
            alt_headers = dict(headers)
            alt_headers["model"] = "s2.1-pro-free"
            try:
                alt_req = urllib.request.Request(
                    endpoint_url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers=alt_headers,
                    method="POST",
                )
                with urllib.request.urlopen(alt_req, timeout=30.0) as resp:
                    wav_bytes = resp.read()
                    if wav_bytes:
                        return wav_bytes
            except Exception as retry_exc:
                log.warn(f"Fish Audio free tier retry failed: {retry_exc}")
        raise RuntimeError(f"Fish Audio API error ({err.code}): {err_body or err.reason}") from err
    except Exception as exc:
        raise RuntimeError(f"Fish Audio synthesis error: {exc}") from exc


def _synthesize_openai_speech(text: str, config: VoiceConfig) -> bytes:
    """WAV bytes from OpenAI-compatible TTS endpoint (Microsoft Foundry Relay /v1/audio/speech)."""
    import json
    import urllib.request

    endpoint_url = (
        getattr(config, "tts_endpoint", None)
        or os.environ.get("JARVIS_TTS_ENDPOINT")
        or getattr(config, "azure_speech_endpoint", None)
        or os.environ.get("AZURE_SPEECH_ENDPOINT")
        or "http://localhost:8000/v1/audio/speech"
    )
    if not endpoint_url.endswith("/audio/speech"):
        endpoint_url = endpoint_url.rstrip("/") + "/audio/speech"

    model_name = (
        getattr(config, "tts_model", None)
        or os.environ.get("JARVIS_TTS_MODEL")
        or getattr(config, "model", None)
        or "tts-1"
    )
    voice_name = (
        getattr(config, "tts_voice", None)
        or getattr(config, "azure_speech_voice", None)
        or os.environ.get("JARVIS_TTS_VOICE")
        or os.environ.get("AZURE_SPEECH_VOICE")
        or getattr(config, "voice", None)
        or "en-US-OnyxTurboMultilingualNeural"
    )
    api_key = (
        _get_azure_speech_key_dynamic(config)
        or getattr(config, "azure_speech_key", None)
        or os.environ.get("AZURE_SPEECH_KEY")
        or os.environ.get("AZURE_API_KEY")
        or os.environ.get("JARVIS_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or "SATVIKNOOB"
    )

    payload = {
        "model": model_name,
        "input": text,
        "voice": voice_name,
        "response_format": "wav",
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    req = urllib.request.Request(
        endpoint_url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30.0) as resp:
        wav_bytes = resp.read()
        if not wav_bytes:
            raise RuntimeError(f"OpenAI/Foundry TTS returned empty audio stream from {endpoint_url}")
        return wav_bytes


def _synthesize_azure_speech(text: str, config: VoiceConfig) -> bytes:
    """WAV bytes synthesized via Microsoft Azure Cognitive Services Speech SDK or Foundry Relay."""
    endpoint_url = (
        getattr(config, "azure_speech_endpoint", None)
        or os.environ.get("AZURE_SPEECH_ENDPOINT")
        or os.environ.get("JARVIS_AZURE_SPEECH_ENDPOINT")
        or getattr(config, "tts_endpoint", None)
        or os.environ.get("JARVIS_TTS_ENDPOINT")
        or "https://satviksingh-resource.cognitiveservices.azure.com/"
    )

    # If endpoint points to local Foundry relay or OpenAI-compatible endpoint
    if "localhost" in endpoint_url or "127.0.0.1" in endpoint_url or "audio/speech" in endpoint_url or (endpoint_url.startswith("http://") and not endpoint_url.startswith("https://")):
        return _synthesize_openai_speech(text, config)

    import azure.cognitiveservices.speech as speechsdk
    from urllib.parse import urlparse

    parsed = urlparse(endpoint_url)
    base_endpoint = f"{parsed.scheme}://{parsed.netloc}"

    speech_key = _get_azure_speech_key_dynamic(config)

    voice_name = (
        getattr(config, "azure_speech_voice", None)
        or os.environ.get("AZURE_SPEECH_VOICE")
        or os.environ.get("JARVIS_AZURE_SPEECH_VOICE")
        or "en-US-OnyxTurboMultilingualNeural"
    )

    if speech_key:
        speech_config = speechsdk.SpeechConfig(subscription=speech_key, endpoint=base_endpoint)
    else:
        # Attempt Entra ID / SmartAzureCredential token if key is omitted
        try:
            from ..auth.azure_auth import get_azure_credential
            cred = get_azure_credential()
            token = cred.get_token("https://cognitiveservices.azure.com/.default")
            speech_config = speechsdk.SpeechConfig(auth_token=f"aad#{token.token}", endpoint=base_endpoint)
        except Exception as auth_exc:
            raise RuntimeError(
                "Azure Speech Key is required. Please set AZURE_SPEECH_KEY in your .env file or config.yaml.\n"
                f"Authentication attempt failed: {auth_exc}"
            ) from auth_exc

    speech_config.speech_synthesis_voice_name = voice_name
    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm
    )

    # In-memory WAV synthesis (audio_config=None) allows Jarvis full-duplex interruptible playback
    speech_synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)

    result = speech_synthesizer.speak_text_async(text).get()

    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        wav_bytes = result.audio_data
        if not wav_bytes:
            raise RuntimeError("Azure Speech synthesized empty audio stream")
        return wav_bytes
    elif result.reason == speechsdk.ResultReason.Canceled:
        cancellation_details = result.cancellation_details
        err_msg = f"Azure Speech synthesis canceled: {cancellation_details.reason}"
        if cancellation_details.reason == speechsdk.CancellationReason.Error:
            err_msg += f" - Error details: {cancellation_details.error_details}"
        raise RuntimeError(err_msg)
    else:
        raise RuntimeError(f"Azure Speech synthesis failed with reason: {result.reason}")


def speak_azure_speech_direct(text: str, config: VoiceConfig | None = None) -> bool:
    """Directly speak text aloud using Azure Speech SDK or Foundry Relay."""
    if is_live_mode_active():
        return False
    cfg = config or _current_voice_snapshot().config
    endpoint_url = (
        getattr(cfg, "azure_speech_endpoint", None)
        or os.environ.get("AZURE_SPEECH_ENDPOINT")
        or getattr(cfg, "tts_endpoint", None)
        or os.environ.get("JARVIS_TTS_ENDPOINT")
        or "https://satviksingh-resource.cognitiveservices.azure.com/"
    )
    if "localhost" in endpoint_url or "127.0.0.1" in endpoint_url or "audio/speech" in endpoint_url or (endpoint_url.startswith("http://") and not endpoint_url.startswith("https://")):
        try:
            wav_data = _synthesize_openai_speech(text, cfg)
            _play_wav(wav_data, wait=True)
            return True
        except Exception as exc:
            log.warn(f"Direct Foundry speech playback failed: {exc}")
            return False

    try:
        import azure.cognitiveservices.speech as speechsdk
    except ImportError:
        return False

    from urllib.parse import urlparse

    parsed = urlparse(endpoint_url)
    base_endpoint = f"{parsed.scheme}://{parsed.netloc}"

    speech_key = _get_azure_speech_key_dynamic(cfg)
    voice_name = (
        getattr(cfg, "azure_speech_voice", None)
        or os.environ.get("AZURE_SPEECH_VOICE")
        or "en-US-OnyxTurboMultilingualNeural"
    )

    if not speech_key:
        return False

    speech_config = speechsdk.SpeechConfig(subscription=speech_key, endpoint=base_endpoint)
    speech_config.speech_synthesis_voice_name = voice_name

    # Use default speaker as audio output
    speech_synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config)
    result = speech_synthesizer.speak_text_async(text).get()

    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        log.info(f"Azure Speech synthesized for text [{text[:40]}...]")
        return True
    elif result.reason == speechsdk.ResultReason.Canceled:
        cancellation_details = result.cancellation_details
        log.warn(f"Azure Speech synthesis canceled: {cancellation_details.reason}")
        if cancellation_details.reason == speechsdk.CancellationReason.Error:
            log.error(f"Azure Speech error details: {cancellation_details.error_details}")
    return False


class _SupersededSpeech(Exception):
    pass


def _synthesize_wav(
    text: str,
    snapshot: _VoiceSnapshot | None = None,
    generation: int | None = None,
) -> bytes:
    """WAV bytes from the multi-tier fail-safe TTS engine.

    Cascades smoothly through:
    1. Preferred engine: Azure Cognitive Services Speech, Kokoro Local ONNX (offline default), Windows SAPI, Edge-TTS, or Gemini
    2. Local Kokoro ONNX (if available offline)
    3. Windows Native SAPI5 / pyttsx3 (100% offline fallback)
    4. Edge-TTS Neural (Free online fallback)
    5. Azure Cognitive Services Speech (if configured)
    6. Gemini Cloud TTS (cloud fallback)
    """
    global _kokoro_broken, _gemini_broken, _edge_broken, _azure_speech_broken, _fish_audio_broken
    snapshot = snapshot or _current_voice_snapshot()
    config = snapshot.config
    raw_engine = (getattr(config, "engine", "kokoro") or "kokoro").lower()
    engine_pref = "kokoro" if raw_engine in ("kokoro", "local") else raw_engine

    with _speech_state:
        if (
            snapshot.epoch != _voice_epoch
            or (
                generation is not None
                and generation != _speech_generation
            )
        ):
            raise _SupersededSpeech()

    # 0. Preferred engine: Foundry Relay / OpenAI TTS
    if engine_pref in ("foundry", "openai", "openai_tts", "relay"):
        try:
            return _synthesize_openai_speech(text, config)
        except Exception as exc:
            log.warn(f"Foundry Relay / OpenAI TTS unavailable ({exc}); falling back to local Kokoro/Edge-TTS")

    # 0b. Preferred engine: Azure Cognitive Services Speech SDK
    if engine_pref in ("azure", "azure_speech", "azure-speech", "cognitiveservices"):
        azure_key = _get_azure_speech_key_dynamic(config)
        if _azure_speech_broken and azure_key:
            _azure_speech_broken = False
        if not _azure_speech_broken:
            try:
                return _synthesize_azure_speech(text, config)
            except Exception as exc:
                _azure_speech_broken = True
                log.warn(f"Azure Cognitive Services Speech TTS unavailable ({exc}); falling back to local Kokoro/Edge-TTS")

    # 0c. Preferred engine: Fish Audio API (https://fish.audio)
    if engine_pref in ("fish", "fish_audio", "fish-audio", "fishaudio"):
        fish_key = _get_fish_audio_key_dynamic(config)
        if _fish_audio_broken and fish_key:
            _fish_audio_broken = False
        if not _fish_audio_broken:
            try:
                return _synthesize_fish_audio(text, config)
            except Exception as exc:
                _fish_audio_broken = True
                log.warn(f"Fish Audio TTS unavailable ({exc}); falling back to local Kokoro/Edge-TTS")

    # 1. Preferred engine: Kokoro (local offline default)
    if engine_pref == "kokoro" and not _kokoro_broken:
        try:
            return _synthesize_kokoro(text, config)
        except Exception as exc:
            _kokoro_broken = True
            log.warn(f"Local Kokoro TTS unavailable ({exc}); falling back to Windows SAPI offline engine")

    # 2. Preferred engine: SAPI (100% offline Windows native)
    if engine_pref in ("sapi", "system"):
        try:
            return _synthesize_sapi(text, config)
        except Exception as exc:
            log.warn(f"Windows SAPI TTS failed ({exc}); trying Kokoro/Edge-TTS")

    # 3. Preferred engine: Gemini Cloud TTS
    if engine_pref == "gemini" and not _gemini_broken:
        try:
            pcm = _synthesize(text, snapshot)
            return _wav_bytes(pcm)
        except Exception as exc:
            _gemini_broken = True
            log.warn(f"Gemini Cloud TTS unavailable ({exc}); switching to Edge-TTS neural engine")

    # 4. Preferred engine: Edge-TTS Neural
    if engine_pref in ("edge", "edge_tts", "edge-tts") and not _edge_broken:
        try:
            return _synthesize_edge_tts(text, config)
        except Exception as exc:
            _edge_broken = True
            log.warn(f"Edge-TTS unavailable ({exc}); falling back to Windows SAPI offline engine")

    # 5. Local Offline Tier-2: Kokoro (if not tried yet)
    if not _kokoro_broken:
        try:
            return _synthesize_kokoro(text, config)
        except Exception:
            _kokoro_broken = True

    # 6. Local Offline Tier-3: 100% Offline Windows Native SAPI
    try:
        return _synthesize_sapi(text, config)
    except Exception as exc:
        log.warn(f"Offline SAPI fallback failed ({exc}); attempting Edge-TTS")

    # 7. Universal Tier-4: Edge-TTS Neural (fast, free, natural)
    if not _edge_broken:
        try:
            return _synthesize_edge_tts(text, config)
        except Exception as exc:
            _edge_broken = True
            log.warn(f"Edge-TTS fallback failed ({exc})")

    # 8. Cloud Tier-5: Azure Cognitive Services Speech (if configured and not broken)
    azure_fallback_key = _get_azure_speech_key_dynamic(config)
    if not _azure_speech_broken and azure_fallback_key:
        try:
            return _synthesize_azure_speech(text, config)
        except Exception as exc:
            _azure_speech_broken = True
            log.warn(f"Azure Speech fallback failed ({exc})")

    # 9. Cloud Tier-6: Gemini Cloud TTS (ONLY if engine was explicitly configured as gemini)
    if engine_pref == "gemini" and not _gemini_broken and getattr(snapshot, "brain", None) is not None:
        try:
            pcm = _synthesize(text, snapshot)
            return _wav_bytes(pcm)
        except Exception:
            _gemini_broken = True

    raise RuntimeError("All TTS synthesis engines failed.")


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


def _play_stream_interruptible(
    data: bytes,
    cancel_event: threading.Event | None = None,
) -> tuple[bool, float]:
    """Play WAV data using streaming chunks with sub-50ms cancellation checking.

    Returns (was_interrupted: bool, played_seconds: float).
    """
    global _active_playback_cancel, _is_speaking_flag
    if not data:
        return False, 0.0

    _stop_async_playback()
    cancel = cancel_event or threading.Event()
    with _active_playback_lock:
        _active_playback_cancel = cancel
        _is_speaking_flag = True

    try:
        with wave.open(io.BytesIO(data), "rb") as wf:
            rate = wf.getframerate()
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            frames_total = wf.getnframes()
            duration = frames_total / rate if rate else 0.0

            if sampwidth != 2 or channels not in (1, 2):
                _play_wav(data, wait=True)
                return False, duration

            chunk_frames = max(512, rate // 20)  # ~50ms per chunk
            played_frames = 0

            try:
                import sounddevice as sd
                with sd.RawOutputStream(
                    samplerate=rate,
                    channels=channels,
                    dtype="int16",
                    blocksize=chunk_frames,
                ) as stream:
                    while played_frames < frames_total:
                        if cancel.is_set():
                            return True, played_frames / rate
                        raw_chunk = wf.readframes(chunk_frames)
                        if not raw_chunk:
                            break
                        stream.write(raw_chunk)
                        played_frames += len(raw_chunk) // (channels * 2)

                return False, played_frames / rate
            except Exception:
                if not cancel.is_set():
                    _play_wav(data, wait=True)
                return False, duration
    except Exception:
        return False, 0.0
    finally:
        with _active_playback_lock:
            if _active_playback_cancel is cancel:
                _active_playback_cancel = None
            _is_speaking_flag = False



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
        # Interrupt any active background playback so sync speech plays immediately
        with _active_playback_lock:
            if _active_playback_cancel and not _active_playback_cancel.is_set():
                _active_playback_cancel.set()
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
    if is_live_mode_active():
        return
    raw_text = (text or "").strip()
    if not raw_text:
        return
    clean = _clean_for_speech(raw_text) or raw_text
    if not clean:
        return
    if wait:
        try:
            _speak_sync(clean)
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
        _SPEECH_DISPATCHER.submit(clean, generation, snapshot)


def speak_to_wav(text: str, path: str) -> bool:
    """Render speech to a WAV file (used by the self-test; no speakers needed)."""
    try:
        clean = _clean_for_speech(text) or text
        snapshot = _current_voice_snapshot()
        with open(path, "wb") as output:
            output.write(_synthesize_wav(clean, snapshot=snapshot))
        return True
    except Exception as exc:
        log.warn(f"TTS-to-file failed: {exc}")
        return False


# --------------------------------------------------------------------------- #
# Wake word
# --------------------------------------------------------------------------- #

# Live wake-word listening runs entirely on ONE dedicated background thread,
# started lazily and reused for the rest of the process's life (see
# _wake_worker_loop / _ensure_wake_worker). A naive per-call implementation
# had two real bugs:
#
#  1. SpInprocRecognizer.AudioInput = Dispatch("SAPI.SpMMAudioIn") reliably
#     raises a COM "Type mismatch" (-2147352571) - pywin32's dynamic dispatch
#     can't marshal that object into the property - so live listening failed
#     before it ever started. Fix: use SpSharedRecognizer, which manages the
#     OS's default microphone itself and needs no AudioInput at all.
#  2. Building a fresh recognizer/context/grammar - and calling CoInitialize/
#     CoUninitialize - on EVERY call worked once and then hung forever on the
#     next call: tearing down a shared-recognizer context races the SAPI
#     engine's own async event delivery, so the following CreateRecoContext
#     blocked indefinitely waiting on a call that would never be pumped.
#
# On top of that, SAPI's shared engine can itself hang or time out starting
# its out-of-process server - especially with another app (a dictation tool,
# etc.) also driving the microphone - and that hang has no exception and no
# timeout of its own to catch. Running it on its own thread means a wedged
# SAPI call can never freeze the console/agent loop: wait_for_wake() below
# gives that thread a bounded grace period to prove it is alive, then gives
# up on it (leaving it to die on its own, harmlessly, as a daemon thread)
# rather than hanging Jarvis with it.

_wake_worker: dict | None = None
_wake_worker_lock = threading.Lock()
_WAKE_STARTUP_GRACE = 25.0    # generous: covers a couple of slow-start retries


def _wake_worker_loop(requests: "queue.Queue") -> None:
    """Body of the dedicated wake-word thread.

    Owns COM and the SAPI recognizer/context/grammar for as long as the
    process runs. Each request is
    ``(phrase, timeout, ready_q, done_q, cancel_event)``.
    """
    import time as _t
    import pythoncom  # type: ignore
    import win32com.client  # type: ignore

    pythoncom.CoInitialize()
    ctx = grammar = heard = None
    cached_phrase = None

    while True:
        phrase, timeout, ready_q, done_q, cancel_event = requests.get()
        try:
            if grammar is None or cached_phrase != phrase:
                # Starting the shared engine's out-of-process server
                # occasionally times out on a loaded machine
                # (SPERR_REMOTE_CALL_TIMED_OUT_START, OLE error 0x80045071) -
                # observed to be transient, so retry once with a short pause
                # before giving up.
                attempts = 2
                for attempt in range(attempts):
                    try:
                        rec = win32com.client.Dispatch("SAPI.SpSharedRecognizer")
                        ctx = rec.CreateRecoContext()
                        break
                    except Exception as exc:
                        if attempt == attempts - 1:
                            raise
                        log.warn(f"wake-word engine starting slowly, retrying: {exc}")
                        _t.sleep(1.0)
                grammar = ctx.CreateGrammar()
                rule = grammar.Rules.Add("wake", 1 | 2)   # SRATopLevel|SRADynamic
                rule.InitialState.AddWordTransition(None, phrase)
                grammar.Rules.Commit()

                heard = []

                class _Sink:
                    def OnRecognition(self, *args):
                        heard.append(1)

                win32com.client.WithEvents(ctx, _Sink)
                cached_phrase = phrase
            ready_q.put(("ok", None))
        except Exception as exc:
            grammar = None      # force a full rebuild attempt next time
            try:
                ready_q.put(("error", exc))
            except Exception:
                pass
            continue

        try:
            heard.clear()
            grammar.CmdSetRuleState("wake", 1)            # SGDSActive
            t0 = _t.time()
            result = False
            while True:
                pythoncom.PumpWaitingMessages()
                if heard:
                    result = True
                    break
                if cancel_event.is_set():
                    break
                if timeout is not None and _t.time() - t0 > timeout:
                    break
                _t.sleep(0.05)
            done_q.put(("ok", result))
        except Exception as exc:
            try:
                done_q.put(("error", exc))
            except Exception:
                pass
        finally:
            try:
                grammar.CmdSetRuleState("wake", 0)
            except Exception:
                pass


def _ensure_wake_worker() -> dict:
    """Return the shared worker state, spawning a fresh thread if there is
    none yet, or if the previous one has actually exited."""
    global _wake_worker
    with _wake_worker_lock:
        if _wake_worker is None or not _wake_worker["thread"].is_alive():
            requests: queue.Queue = queue.Queue()
            thread = threading.Thread(
                target=_wake_worker_loop, args=(requests,),
                daemon=True, name="jarvis-wake-word")
            thread.start()
            _wake_worker = {"thread": thread, "requests": requests}
        return _wake_worker


def _drop_wake_worker(worker: dict) -> None:
    """Abandon a worker that failed to respond in time.

    It may still be blocked inside a hung native COM call forever; that is
    fine - it is a daemon thread, so the process can still exit normally,
    and the next call simply builds a fresh worker with its own apartment.
    """
    global _wake_worker
    with _wake_worker_lock:
        if _wake_worker is worker:
            _wake_worker = None


def wait_for_wake(phrase: str = "hey jarvis", timeout: float | None = None,
                  _wav_file: str | None = None) -> bool:
    """Block until the wake phrase is spoken. Offline, near-zero CPU.

    Uses Windows' built-in SAPI recognizer with a one-phrase grammar - no
    audio ever leaves the machine while waiting. Returns True on wake,
    False on timeout, on any listener failure, or if the engine does not
    respond within a short startup grace period - so a wedged SAPI server
    can never hang the caller. Ctrl+C propagates so the caller can exit
    hands-free mode.

    ``_wav_file`` feeds a file instead of the microphone (self-tests); that
    path is one-shot, in-process, and does not use the background worker.
    """
    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
    except Exception as exc:
        log.warn(f"wake word needs pywin32: {exc}")
        return False

    if _wav_file:
        import time as _t
        pythoncom.CoInitialize()
        grammar = None
        try:
            rec = win32com.client.Dispatch("SAPI.SpInprocRecognizer")
            stream = win32com.client.Dispatch("SAPI.SpFileStream")
            stream.Open(_wav_file)
            rec.AudioInputStream = stream
            ctx = rec.CreateRecoContext()
            grammar = ctx.CreateGrammar()
            rule = grammar.Rules.Add("wake", 1 | 2)
            rule.InitialState.AddWordTransition(None, phrase)
            grammar.Rules.Commit()
            grammar.CmdSetRuleState("wake", 1)

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
                    grammar.CmdSetRuleState("wake", 0)
            except Exception:
                pass
            pythoncom.CoUninitialize()

    worker = _ensure_wake_worker()
    ready_q: queue.Queue = queue.Queue(maxsize=1)
    done_q: queue.Queue = queue.Queue(maxsize=1)
    cancel_event = threading.Event()
    worker["requests"].put((phrase, timeout, ready_q, done_q, cancel_event))

    try:
        status, err = ready_q.get(timeout=_WAKE_STARTUP_GRACE)
    except queue.Empty:
        log.warn("wake-word engine did not respond in time; is another app "
                 "(a dictation tool, etc.) also using the microphone?")
        cancel_event.set()
        _drop_wake_worker(worker)
        return False
    if status == "error":
        log.warn(f"wake-word listener failed: {err}")
        return False

    try:
        status, result = done_q.get(timeout=timeout)
    except queue.Empty:
        cancel_event.set()
        return False
    except BaseException:
        # Ctrl+C (or anything else) while waiting: tell the worker to stop
        # this wait right away so it is free for the next request instead of
        # sitting blocked on an abandoned, possibly-unbounded wait.
        cancel_event.set()
        raise
    if status == "error":
        log.warn(f"wake-word listener failed: {result}")
        return False
    return result


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


class _BargeInVAD(_EnergyVAD):
    """Energy VAD with adaptive acoustic feedback suppression & barge-in detection."""

    def __init__(
        self,
        silence_after: float,
        chunk: float = 0.1,
        barge_in_sensitivity: float = 0.5,
        barge_in_hold: int = 2,
    ):
        super().__init__(silence_after=silence_after, chunk=chunk)
        self.barge_in_sensitivity = max(0.1, min(1.0, barge_in_sensitivity))
        self.barge_in_hold = max(1, barge_in_hold)
        self.barge_in_factor = 4.2 * (1.0 - (self.barge_in_sensitivity - 0.5) * 0.4)
        self.barge_hold = 0

    def feed(self, level: float, is_speaking: bool = False) -> str:
        """Feed an RMS level. Returns 'listening', 'interrupt', 'recording', or 'stop'."""
        if not self.started:
            floor = max(self.MIN_FLOOR, self.floor if self.floor is not None else level)
            if self.warmed < self.WARMUP:
                self.warmed += 1
                self._track_floor(level)
                return "listening"

            threshold_factor = self.barge_in_factor if is_speaking else self.START_FACTOR
            required_hold = self.barge_in_hold if is_speaking else self.START_HOLD

            if level >= threshold_factor * floor:
                self.hold += 1
                if self.hold >= required_hold:
                    self.started = True
                    self.speech_ref = level
                    return "interrupt" if is_speaking else "recording"
                return "listening"

            self.hold = 0
            if not is_speaking:
                self._track_floor(level)
            return "listening"

        # Active recording phase
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


def record_until_cancelled(
    stop_event: threading.Event,
    max_seconds: float = 120.0,
) -> bytes | None:
    """Continuously record microphone audio until `stop_event` is set.

    Used by Push-to-Talk toggle (press hotkey to start, press hotkey to stop).
    Does NOT stop on silence or pause.
    Returns standard WAV bytes, or None if no audio or canceled immediately.
    """
    try:
        import sounddevice as sd  # type: ignore
    except Exception as exc:
        log.warn(f"microphone unavailable: {exc}")
        return None

    frames: list[bytes] = []
    try:
        with sd.RawInputStream(
            samplerate=_RATE,
            channels=1,
            dtype="int16",
            blocksize=_CHUNK,
        ) as stream:
            while not stop_event.is_set():
                chunk, _ = stream.read(_CHUNK)
                chunk = bytes(chunk)
                frames.append(chunk)
                if len(frames) * 0.1 >= max_seconds:
                    break
    except Exception as exc:
        log.warn(f"Push-to-talk recording failed: {exc}")
        return None

    if not frames or len(frames) < 2:
        return None

    secs = len(frames) * 0.1
    log.info(f"🎤 Push-to-talk recorded {secs:.1f}s")

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(_RATE)
        wf.writeframes(b"".join(frames))
    return buf.getvalue()


def speak_and_listen(
    text: str,
    start_timeout: float = 8.0,
    max_seconds: float = 15.0,
    silence_after: float | None = None,
    full_duplex: bool = True,
) -> tuple[bytes | None, bool]:
    """Speak text while concurrently listening for speech interruption (Full-Duplex Barge-in).

    If the user speaks while Jarvis is talking:
      - Instantly aborts playback (<50ms)
      - Captures user speech from onset
      - Returns (user_wav_bytes, was_interrupted=True)

    If playback finishes and user speaks afterwards:
      - Returns (user_wav_bytes, was_interrupted=False)

    If nothing is heard / silence:
      - Returns (None, was_interrupted=False)
    """
    text = (text or "").strip()
    if not text:
        wav = listen(start_timeout=start_timeout, max_seconds=max_seconds, silence_after=silence_after)
        return wav, False

    if not full_duplex or not getattr(_tts_config, "full_duplex", True):
        speak(text, wait=True)
        wav = listen(start_timeout=start_timeout, max_seconds=max_seconds, silence_after=silence_after)
        return wav, False

    snapshot = _current_voice_snapshot()
    try:
        clean = _clean_for_speech(text) or text
        data = _synthesize_wav(clean, snapshot=snapshot)
    except Exception as exc:
        log.warn(f"TTS synthesis failed: {exc}")
        data = b""

    if not data:
        wav = listen(start_timeout=start_timeout, max_seconds=max_seconds, silence_after=silence_after)
        return wav, False

    try:
        import sounddevice as sd
    except Exception as exc:
        log.warn(f"sounddevice unavailable for full-duplex: {exc}")
        _play_wav(data, wait=True)
        wav = listen(start_timeout=start_timeout, max_seconds=max_seconds, silence_after=silence_after)
        return wav, False

    silence_val = silence_after if silence_after is not None else _tts_config.silence_after
    vad = _BargeInVAD(
        silence_after=silence_val,
        barge_in_sensitivity=_tts_config.barge_in_sensitivity,
        barge_in_hold=_tts_config.barge_in_hold,
    )

    cancel_event = threading.Event()
    playback_done = threading.Event()
    interrupted = False

    def _playback_thread():
        try:
            _play_stream_interruptible(data, cancel_event=cancel_event)
        finally:
            playback_done.set()

    t_play = threading.Thread(target=_playback_thread, daemon=True, name="jarvis-duplex-play")
    t_play.start()

    from collections import deque
    preroll = deque(maxlen=max(3, _tts_config.barge_in_hold + 1))
    frames: list[bytes] = []
    waited = 0.0

    def rms(chunk: bytes) -> float:
        n = len(chunk) // 2
        if not n:
            return 0.0
        samples = struct.unpack(f"<{n}h", chunk)
        return math.sqrt(sum(s * s for s in samples) / n)

    try:
        with sd.RawInputStream(samplerate=_RATE, channels=1, dtype="int16", blocksize=_CHUNK) as stream:
            while True:
                chunk, _ = stream.read(_CHUNK)
                chunk = bytes(chunk)
                speaking_now = not playback_done.is_set()

                state = vad.feed(rms(chunk), is_speaking=speaking_now)

                if state == "listening":
                    preroll.append(chunk)
                    if not speaking_now:
                        waited += 0.1
                        if waited >= start_timeout:
                            break
                    continue

                if state in {"interrupt", "recording"}:
                    if speaking_now and not interrupted:
                        interrupted = True
                        cancel_event.set()
                        log.info("⚡ Voice interruption (Barge-in) detected!")

                    if not frames:
                        frames.extend(preroll)
                    frames.append(chunk)

                    if state == "stop" or len(frames) * 0.1 >= max_seconds:
                        break
    except Exception as exc:
        log.warn(f"Full-duplex mic stream error: {exc}")
        cancel_event.set()

    cancel_event.set()
    t_play.join(timeout=1.0)

    if not frames:
        return None, interrupted

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(_RATE)
        wf.writeframes(b"".join(frames))

    return buf.getvalue(), interrupted



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
