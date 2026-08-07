"""Terminal worker used by :mod:`jarvis.browser`.

This process runs the regular console REPL unchanged.  The only additions are
an input shim (pipes cannot use Windows ``msvcrt.getwch``) and structured
stderr events that let the browser animate without scraping terminal art.
"""

from __future__ import annotations

from array import array
import builtins
import base64
import functools
import importlib.util
import io
import json
import math
from pathlib import Path
import re
import sys
import threading
from typing import Any, Callable
import wave


EVENT_PREFIX = "__JARVIS_BROWSER_EVENT__:"
INPUT_PREFIX = "__JARVIS_BROWSER_INPUT64__:"
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_emit_lock = threading.Lock()
_speech_lock = threading.Lock()
_speech_generation = 0
_active_speech_generation: int | None = None
_SPECTRUM_BANDS = 28
_SPECTRUM_FPS = 30
_SPECTRUM_MAX_FRAMES = _SPECTRUM_FPS * 60
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if not sys.path or sys.path[0] != str(_PROJECT_ROOT):
    sys.path.insert(0, str(_PROJECT_ROOT))


def _load_launcher() -> Any:
    """Load this checkout's launcher without consulting the working cwd."""
    root = str(_PROJECT_ROOT)
    if not sys.path or sys.path[0] != root:
        sys.path.insert(0, root)
    spec = importlib.util.spec_from_file_location(
        "_jarvis_browser_launcher",
        _PROJECT_ROOT / "run.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load the Jarvis launcher")
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    return launcher


def _plain(value: Any) -> str:
    return _ANSI_RE.sub("", str(value)).strip()


def emit(event: str, **payload: Any) -> None:
    """Write one atomic machine-readable event without touching stdout."""
    record = {"event": event, **payload}
    try:
        encoded = json.dumps(record, ensure_ascii=False, default=str)
        with _emit_lock:
            sys.stderr.write(EVENT_PREFIX + encoded + "\n")
            sys.stderr.flush()
    except Exception:
        # The visual observer must never be able to break the agent backend.
        pass


def _state_for_activity(kind: str, message: str) -> str | None:
    text = message.lower()
    if kind == "info":
        if "perceiv" in text or "screenshot" in text or "attached image" in text:
            return "perceiving"
        if "listening" in text:
            return "listening"
        if "transcrib" in text:
            return "transcribing"
        return None
    if kind == "step":
        return "planning" if ("plan" in text or text.startswith("task:")) else "working"
    return {
        "think": "thinking",
        "act": "acting",
        "ok": "success",
        "warn": "warning",
        "error": "error",
    }.get(kind)


def _wrap_activity(log_module: Any, name: str) -> None:
    original = getattr(log_module, name)

    @functools.wraps(original)
    def wrapped(message: Any, *args: Any, **kwargs: Any):
        text = _plain(message)
        emit("activity", kind=name, message=text)
        state = _state_for_activity(name, text)
        if state:
            emit("state", state=state, label=text[:120])
        return original(message, *args, **kwargs)

    setattr(log_module, name, wrapped)


def _wav_profile(data: bytes, points: int = 72) -> tuple[float, list[float]]:
    """Return playback duration plus a compact real-amplitude envelope."""
    try:
        with wave.open(io.BytesIO(data), "rb") as wav_file:
            frame_rate = wav_file.getframerate()
            frame_count = wav_file.getnframes()
            sample_width = wav_file.getsampwidth()
            raw = wav_file.readframes(frame_count)
        duration = frame_count / frame_rate if frame_rate else 0.0
        if sample_width != 2 or not raw:
            return round(duration, 3), []

        samples = array("h")
        samples.frombytes(raw)
        if sys.byteorder != "little":
            samples.byteswap()
        if not samples:
            return round(duration, 3), []

        bucket_size = max(1, math.ceil(len(samples) / max(1, points)))
        envelope = []
        for start in range(0, len(samples), bucket_size):
            bucket = samples[start:start + bucket_size]
            energy = math.sqrt(
                sum(sample * sample for sample in bucket) / len(bucket)
            )
            envelope.append(energy)

        peak = max(envelope, default=0.0)
        if peak <= 0:
            return round(duration, 3), [0.0 for _ in envelope]
        normalized = [
            round(min(1.0, (value / peak) ** 0.68), 3)
            for value in envelope
        ]
        return round(duration, 3), normalized
    except (EOFError, OSError, ValueError, wave.Error):
        return 0.0, []


def _wav_spectrogram(data: bytes) -> tuple[int, int, str]:
    """Per-frame frequency bands so the voice ring can move band by band.

    Returns ``(band_count, fps, base64)`` where the payload is a
    ``frames x band_count`` uint8 matrix in row-major order.  Returns
    ``(0, 0, "")`` when numpy is missing or the clip is unusable; the browser
    then falls back to the flat amplitude envelope.
    """
    try:
        import numpy as np
    except ImportError:
        return 0, 0, ""
    try:
        with wave.open(io.BytesIO(data), "rb") as wav_file:
            rate = wav_file.getframerate()
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            raw = wav_file.readframes(wav_file.getnframes())
    except (EOFError, OSError, ValueError, wave.Error):
        return 0, 0, ""
    if sample_width != 2 or not raw or rate <= 0 or channels <= 0:
        return 0, 0, ""

    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32)
    if channels > 1:
        usable = samples.size - samples.size % channels
        samples = samples[:usable].reshape(-1, channels).mean(axis=1)
    if samples.size < 2:
        return 0, 0, ""

    hop = max(1, rate // _SPECTRUM_FPS)
    window_size = 1 << max(8, (hop * 2 - 1).bit_length())
    frames = min(_SPECTRUM_MAX_FRAMES, max(1, -(-samples.size // hop)))
    padded = np.zeros((frames - 1) * hop + window_size, dtype=np.float32)
    copied = min(samples.size, padded.size)
    padded[:copied] = samples[:copied]

    window = np.hanning(window_size).astype(np.float32)
    chunks = np.lib.stride_tricks.sliding_window_view(padded, window_size)
    spectra = np.abs(np.fft.rfft(chunks[::hop][:frames] * window, axis=1))

    # Log-spaced edges: speech energy is bunched at the bottom, so linear bands
    # would leave most of the ring dead.
    edges = np.geomspace(80.0, max(160.0, min(7600.0, rate / 2 - 1)),
                         _SPECTRUM_BANDS + 1)
    bins = np.clip((edges * window_size / rate).astype(int),
                   0, window_size // 2)
    matrix = np.zeros((frames, _SPECTRUM_BANDS), dtype=np.float32)
    for band in range(_SPECTRUM_BANDS):
        low = bins[band]
        high = max(low + 1, bins[band + 1])
        matrix[:, band] = spectra[:, low:high].mean(axis=1)

    matrix = np.log1p(matrix / 32.0)
    peak = float(matrix.max())
    if peak <= 0:
        return 0, 0, ""
    scaled = np.clip(matrix / peak, 0.0, 1.0) ** 0.85
    payload = (scaled * 255.0).astype(np.uint8).tobytes()
    return (
        _SPECTRUM_BANDS,
        _SPECTRUM_FPS,
        base64.b64encode(payload).decode("ascii"),
    )


def _begin_speech(
    duration: float,
    envelope: list[float],
    spectrum: tuple[int, int, str] = (0, 0, ""),
) -> int:
    global _speech_generation, _active_speech_generation
    band_count, band_fps, bands = spectrum
    with _speech_lock:
        _speech_generation += 1
        generation = _speech_generation
        _active_speech_generation = generation
        # Keep generation assignment and wire emission in the same critical
        # section so concurrent cron/foreground speech cannot reorder starts.
        emit(
            "speech",
            active=True,
            utterance_id=generation,
            duration_ms=max(0, round(duration * 1000)),
            levels=[
                max(0, min(255, round(level * 255)))
                for level in envelope
            ],
            band_count=band_count,
            band_fps=band_fps,
            bands=bands,
        )
    return generation


def _finish_speech(generation: int) -> None:
    global _active_speech_generation
    with _speech_lock:
        if _active_speech_generation != generation:
            return
        _active_speech_generation = None
        emit("speech", active=False, utterance_id=generation)


def _warm_spectrogram_deps() -> None:
    """Import numpy ahead of the first utterance (~90 ms off the hot path)."""
    try:
        import numpy  # noqa: F401
    except ImportError:
        pass


def _install_speech_bridge(voice_module: Any) -> None:
    """Observe WAV playback without changing the shared voice implementation."""
    if getattr(voice_module, "_browser_speech_bridge_installed", False):
        return
    voice_module._browser_speech_bridge_installed = True
    threading.Thread(target=_warm_spectrogram_deps, daemon=True).start()
    original_play = voice_module._play_wav

    @functools.wraps(original_play)
    def browser_play_wav(data: bytes, wait: bool) -> Any:
        duration, envelope = _wav_profile(data)
        generation = _begin_speech(duration, envelope, _wav_spectrogram(data))
        try:
            result = original_play(data, wait)
        except Exception:
            _finish_speech(generation)
            raise
        if wait:
            _finish_speech(generation)
        else:
            timer = threading.Timer(
                max(0.2, duration),
                _finish_speech,
                args=(generation,),
            )
            timer.daemon = True
            timer.start()
        return result

    voice_module._play_wav = browser_play_wav


def install_event_bridge() -> None:
    """Instrument the logger and input boundary for this worker process."""
    import signal
    if hasattr(signal, "SIGBREAK"):
        try:
            signal.signal(signal.SIGBREAK, signal.default_int_handler)
        except Exception:
            pass

    from jarvis.utils import logging as log

    if getattr(log, "_browser_event_bridge_installed", False):
        return
    log._browser_event_bridge_installed = True


    for name in ("info", "step", "think", "act", "ok", "warn", "error"):
        _wrap_activity(log, name)

    original_rule = log.rule

    @functools.wraps(original_rule)
    def rule(label: str = "", color: str = "grey") -> None:
        text = _plain(label)
        if text:
            emit("activity", kind="phase", message=text)
            if text.lower().startswith("done in"):
                emit("state", state="success", label=text)
        return original_rule(label, color)

    log.rule = rule

    original_jarvis = log.jarvis

    @functools.wraps(original_jarvis)
    def jarvis(message: Any) -> None:
        text = str(message)
        emit("assistant", message=text)
        emit("state", state="responding", label="Synthesizing response")
        try:
            from jarvis.sessions import get_session_manager
            sm = get_session_manager()
            session = sm.append_message("assistant", text)
            if not session.has_ai_title and len(session.messages) >= 2:
                def _gen_title():
                    try:
                        from jarvis.config import load_config
                        from jarvis.agent.brain import make_brain
                        cfg = load_browser_config()
                        brain = make_brain(cfg.brain)
                        new_title = sm.generate_ai_title(session, brain)
                        if new_title:
                            emit("session_title_updated", id=session.id, title=new_title)
                            emit("session_list", sessions=sm.list_sessions(), active_id=sm.active_session_id)
                    except Exception:
                        pass
                threading.Thread(target=_gen_title, daemon=True, name="ai-title-gen").start()
        except Exception:
            pass
        return original_jarvis(message)

    log.jarvis = jarvis

    original_pop = log.pop

    @functools.wraps(original_pop)
    def pop(success: bool = True) -> None:
        emit("state", state="success" if success else "warning",
             label="Task complete" if success else "Task ended")
        return original_pop(success)

    log.pop = pop

    original_spinner = log.spinner

    class BrowserSpinner:
        """Decorator around the existing spinner with browser state events."""

        def __init__(self, label: str = "thinking"):
            self.label = label
            self._inner = original_spinner(label)

        def __enter__(self):
            lowered = self.label.lower()
            if "plan" in lowered:
                state = "planning"
            elif "verif" in lowered:
                state = "verifying"
            elif "transcrib" in lowered:
                state = "transcribing"
            elif "enhanc" in lowered:
                state = "thinking"
            else:
                state = "thinking"
            emit("activity", kind="spinner", message=self.label)
            emit("state", state=state, label=self.label)
            self._inner.__enter__()
            return self

        def __exit__(self, *exc: Any):
            result = self._inner.__exit__(*exc)
            if exc and exc[0] is not None:
                emit("state", state="error", label=str(exc[1])[:120])
            else:
                emit("state", state="working", label=self.label or "Processing")
            return result


    log.spinner = BrowserSpinner

    from jarvis.utils import voice

    _install_speech_bridge(voice)

    original_input: Callable[[str], str] = builtins.input

    @functools.wraps(original_input)
    def browser_input(prompt: str = "") -> str:
        text = _plain(prompt)
        lowered = text.lower()
        if "[y/n]" in lowered or lowered.startswith("run "):
            mode = "confirmation"
        elif "answer" in lowered or "what should i do with it" in lowered:
            mode = "answer"
        else:
            mode = "command"
        emit("input_request", prompt=text, mode=mode)
        emit(
            "state",
            state="listening",
            label="Awaiting confirmation" if mode == "confirmation"
            else "Awaiting directive",
        )
        try:
            value = original_input(prompt)
            if value.startswith(INPUT_PREFIX):
                try:
                    value = base64.b64decode(
                        value[len(INPUT_PREFIX):],
                        validate=True,
                    ).decode("utf-8")
                except (ValueError, UnicodeDecodeError):
                    pass
            if value and mode == "command" and not value.startswith(":"):
                try:
                    from jarvis.sessions import get_session_manager
                    get_session_manager().append_message("user", value)
                except Exception:
                    pass
            return value

        finally:
            emit("state", state="working", label="Directive received")

    builtins.input = browser_input

    # The Windows console reader talks directly to msvcrt.  Browser mode has a
    # real terminal process but its stdin is a pipe, so route every REPL prompt
    # through the patched line-oriented input boundary above.
    from jarvis import console

    console._read_input = lambda prompt: builtins.input(prompt)

    # Slash-command output (/help, /config, /memory, etc.) goes to print() →
    # stdout → "terminal" event, which only populates the terminal drawer.
    # Wrap _command so its stdout is also emitted as an "assistant" event so
    # the response appears in the browser chatbox.
    _original_command = console._command

    def _browser_command(cmd: str, cfg: Any) -> bool:
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            result = _original_command(cmd, cfg)
        finally:
            sys.stdout = old_stdout
        output = _ANSI_RE.sub("", buf.getvalue()).strip()
        if output:
            emit("assistant", message=output)
        return result

    console._command = _browser_command


def main(argv: list[str] | None = None) -> int:
    install_event_bridge()
    emit("state", state="booting", label="Initializing local runtime")
    emit("system", message="Terminal backend connected")

    run = _load_launcher()
    original_load_config = run.load_config

    def load_browser_config(*args: Any, **kwargs: Any) -> Any:
        config = original_load_config(*args, **kwargs)
        # Browser mode always needs the typed REPL boundary.  Reply speech is
        # configured separately and remains available exactly as in console
        # mode; only the microphone-only startup loop is suppressed.
        config.voice_enabled = False
        return config

    run.load_config = load_browser_config
    browser_argv = list(sys.argv[1:] if argv is None else argv)
    browser_argv = [value for value in browser_argv if value != "--voice"]

    try:
        return run.main(browser_argv)
    except KeyboardInterrupt:
        emit("state", state="offline", label="Session interrupted")
        return 130
    except Exception as exc:
        emit("activity", kind="error", message=f"worker failed: {exc}")
        emit("state", state="error", label=str(exc)[:120])
        raise


if __name__ == "__main__":
    raise SystemExit(main())
