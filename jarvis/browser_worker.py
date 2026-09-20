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
import os
from pathlib import Path
import re
import sys
import threading
import time
from typing import Any, Callable
import wave


EVENT_PREFIX = "__JARVIS_BROWSER_EVENT__:"
INPUT_PREFIX = "__JARVIS_BROWSER_INPUT64__:"
# Request/response channel for direct action execution (the voice agent's
# tools). Unlike INPUT_PREFIX this is not user input: the runtime runs the
# action and answers with a "tool_result" event, then goes back to waiting.
TOOL_PREFIX = "__JARVIS_BROWSER_TOOL__:"
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_bridge_installed = False
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


def run_tool_request(raw: str) -> None:
    """Execute one direct action request and emit its result.

    Runs on the console's own thread, inside the input boundary - which is
    exactly when Jarvis is idle at a prompt, so a direct call can never race the
    agentic loop over the same desktop.
    """
    call_id = ""
    result: dict[str, Any]
    try:
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise ValueError("tool request must be an object")
        call_id = str(request.get("call_id", ""))
        name = str(request.get("tool", ""))
        args = request.get("args") or {}
        from jarvis.live import direct_tools

        result = direct_tools.run(name, args)
    except Exception as exc:  # a malformed request must not kill the runtime
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    emit(
        "tool_result",
        call_id=call_id,
        ok=bool(result.get("ok")),
        result=str(result.get("result", "")),
        error=str(result.get("error", "")),
    )


def emit(event: str, **payload: Any) -> None:
    """Write one atomic machine-readable event without touching stdout."""
    global _bridge_installed
    if not _bridge_installed:
        try:
            from jarvis.utils import logging as log
            if getattr(log, "_browser_event_bridge_installed", False):
                _bridge_installed = True
        except Exception:
            pass
    if not _bridge_installed:
        return
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
    audio: str = "",
) -> int:
    global _speech_generation, _active_speech_generation
    band_count, band_fps, bands = spectrum
    with _speech_lock:
        _speech_generation += 1
        generation = _speech_generation
        _active_speech_generation = generation
        # Keep generation assignment and wire emission in the same critical
        # section so concurrent cron/foreground speech cannot reorder starts.
        payload = {
            "active": True,
            "utterance_id": generation,
            "duration_ms": max(0, round(duration * 1000)),
            "levels": [
                max(0, min(255, round(level * 255)))
                for level in envelope
            ],
            "band_count": band_count,
            "band_fps": band_fps,
            "bands": bands,
        }
        if audio:
            payload["audio"] = audio
        emit("speech", **payload)
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


def _parent_process_alive(ppid: int | None = None) -> bool:
    """Whether the process that launched this worker is still running.

    Windows has no reparenting, so ``os.getppid()`` keeps returning the pid of
    a parent that has already died; the only reliable test is whether that pid
    still names a live process.  On any doubt - an unreadable pid, a parent we
    are not allowed to open - this reports alive, because the failure we must
    never cause is killing a session that is still in use.
    """
    if ppid is None:
        try:
            ppid = os.getppid()
        except OSError:
            return True
    if not ppid or ppid <= 0:
        return True
    if os.name != "nt":
        try:
            os.kill(ppid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True
        return True

    import ctypes

    SYNCHRONIZE = 0x00100000
    ERROR_INVALID_PARAMETER = 87
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, int(ppid))
    if not handle:
        # ERROR_INVALID_PARAMETER is a pid that no longer exists; anything else
        # (access denied, for instance) means we simply cannot tell.
        return kernel32.GetLastError() != ERROR_INVALID_PARAMETER
    try:
        # 0 (WAIT_OBJECT_0) means the process has exited; WAIT_TIMEOUT means it
        # is still running.  Any failure counts as alive.
        return kernel32.WaitForSingleObject(handle, 0) != 0
    finally:
        kernel32.CloseHandle(handle)


def _watch_parent(ppid: int, poll_seconds: float = 2.0) -> None:
    """Exit when the console that owns this worker disappears.

    Force-killing the launcher used to leave the worker running: it keeps the
    desktop runtime, the proactive daemon and the scheduled jobs alive with no
    UI attached, and a later start then reports stale code and stale state.
    Waiting for stdin to reach EOF is not enough - a worker that is blocked in
    a task, rather than at the prompt, never notices - so the parent is polled
    directly.
    """
    while True:
        time.sleep(poll_seconds)
        if _parent_process_alive(ppid):
            continue
        try:
            emit("activity", kind="warning", message="Console closed; shutting down")
            emit("state", state="offline", label="Console closed")
            sys.stderr.flush()
        except Exception:
            # Nobody is listening any more; exiting cleanly still matters.
            pass
        os._exit(0)


def _install_speech_bridge(voice_module: Any) -> None:
    """Observe WAV playback without changing the shared voice implementation."""
    if getattr(voice_module, "_browser_speech_bridge_installed", False):
        return
    voice_module._browser_speech_bridge_installed = True
    threading.Thread(target=_warm_spectrogram_deps, daemon=True).start()
    original_play = voice_module._play_wav

    @functools.wraps(original_play)
    def browser_play_wav(data: bytes, wait: bool) -> Any:
        if getattr(voice_module, "is_live_mode_active", lambda: False)():
            return None
        duration, envelope = _wav_profile(data)
        audio_b64 = f"data:audio/wav;base64,{base64.b64encode(data).decode('ascii')}" if data else ""
        generation = _begin_speech(duration, envelope, _wav_spectrogram(data), audio=audio_b64)
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

    original_play_stream = getattr(voice_module, "_play_stream_interruptible", None)
    if original_play_stream is not None:
        @functools.wraps(original_play_stream)
        def browser_play_stream(data: bytes, cancel_event: Any = None) -> Any:
            if getattr(voice_module, "is_live_mode_active", lambda: False)():
                return False, 0.0
            import time
            duration, envelope = _wav_profile(data)
            audio_b64 = f"data:audio/wav;base64,{base64.b64encode(data).decode('ascii')}" if data else ""
            generation = _begin_speech(duration, envelope, _wav_spectrogram(data), audio=audio_b64)
            cancel = cancel_event or threading.Event()
            try:
                # The browser tab renders audio through HTML5 Audio (app.js).
                # Pacing here maintains barge-in cancellation and telemetry synchrony.
                step = 0.05
                elapsed = 0.0
                while elapsed < max(0.1, duration):
                    if cancel.is_set():
                        return True, elapsed
                    time.sleep(step)
                    elapsed += step
                return False, elapsed
            finally:
                _finish_speech(generation)

        voice_module._play_stream_interruptible = browser_play_stream



def install_event_bridge() -> None:
    """Instrument the logger and input boundary for this worker process."""
    import signal
    if hasattr(signal, "SIGBREAK"):
        try:
            signal.signal(signal.SIGBREAK, signal.default_int_handler)
        except Exception:
            pass

    from jarvis.utils import logging as log

    global _bridge_installed
    _bridge_installed = True

    if getattr(log, "_browser_event_bridge_installed", False):
        return
    log._browser_event_bridge_installed = True


    for name in ("info", "step", "think", "act", "ok", "warn", "error"):
        _wrap_activity(log, name)

    original_proactive = getattr(log, "proactive", None)
    if original_proactive is not None:
        @functools.wraps(original_proactive)
        def proactive(rule_name: str, message: str, title: str = "", event_type: str = "") -> None:
            emit(
                "proactive_alert",
                title=title or rule_name,
                message=message,
                event_type=event_type,
                rule_name=rule_name,
                timestamp=time.time(),
            )
            return original_proactive(rule_name, message, title, event_type)

        log.proactive = proactive

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
        from jarvis.utils import voice
        if voice.is_live_mode_active():
            # In Live Mode, gpt-realtime is the sole conversational voice and assistant.
            # Background terminal agent chatter (e.g. Side Agent / Communicating Agent)
            # must not emit "assistant" dialogue messages into the chatbox.
            emit("activity", kind="agent", message=text)
            return original_jarvis(message)

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
                        cfg = load_config()
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
            while True:
                value = original_input(prompt)
                if not value.startswith(TOOL_PREFIX):
                    break
                # A direct action request, not a directive: run it here and go
                # back to waiting for the console's real input.
                emit("state", state="working", label="Running direct action")
                run_tool_request(value[len(TOOL_PREFIX):])
                emit(
                    "state",
                    state="listening",
                    label="Awaiting confirmation" if mode == "confirmation"
                    else "Awaiting directive",
                )
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

    # The agent can end its own session. The REPL notices that request on its
    # next turn, but in browser mode that turn may be a long way off: the REPL
    # is parked on a blocking read of its input pipe, which only the parent can
    # write to. Say it out loud instead of waiting, and let the parent close the
    # child down over stdin - the same path the page's END SESSION button takes.
    from jarvis.session_control import add_session_stop_listener

    def _announce_session_stop(record: dict[str, Any]) -> None:
        emit(
            "session_stop",
            reason=str(record.get("reason", "")),
            source=str(record.get("source", "")),
        )
        emit("state", state="offline", label="Session stopped by Jarvis")

    add_session_stop_listener(_announce_session_stop)

    import signal

    def _handle_interrupt_signal(signum: int, frame: Any) -> None:
        try:
            from jarvis.agent.loop import cancel_active_agent
            cancel_active_agent()
        except Exception:
            pass
        try:
            from jarvis.utils import voice
            voice.interrupt_speech()
        except Exception:
            pass
        emit("activity", kind="warn", message="Task execution stopped by user")

    if hasattr(signal, "SIGBREAK"):
        try:
            signal.signal(signal.SIGBREAK, _handle_interrupt_signal)
        except Exception:
            pass
    try:
        signal.signal(signal.SIGINT, _handle_interrupt_signal)
    except Exception:
        pass



def main(argv: list[str] | None = None) -> int:
    install_event_bridge()
    emit("state", state="booting", label="Initializing local runtime")
    emit("system", message="Terminal backend connected")

    # The worker owns the desktop runtime, so it must not outlive the console
    # that opened it. Force-killing the launcher does not close the child's
    # stdin in a way the REPL acts on, so the parent is polled instead.
    if os.environ.get("JARVIS_BROWSER_DETACHED") != "1":
        threading.Thread(
            target=_watch_parent,
            args=(os.getppid(),),
            daemon=True,
            name="jarvis-parent-watch",
        ).start()

    run = _load_launcher()
    original_load_config = run.load_config

    def load_browser_config(*args: Any, **kwargs: Any) -> Any:
        config = original_load_config(*args, **kwargs)
        # Browser mode always needs the typed REPL boundary.  Reply speech is
        # configured separately and remains available exactly as in console
        # mode; only the microphone-only startup loops (voice/wake) are
        # suppressed.
        config.voice_enabled = False
        config.wake_enabled = False
        return config

    run.load_config = load_browser_config
    browser_argv = list(sys.argv[1:] if argv is None else argv)
    browser_argv = [value for value in browser_argv if value not in ("--voice", "--wake")]

    if "--remote-agent" in browser_argv:
        # Add a structured observer to the normal encrypted remote loop.  The
        # worker remains the authority; the page receives display-only state
        # and can answer only the same confirmation/question prompts exposed
        # by the terminal implementation.
        from jarvis import remote

        original_remote_agent = remote.run_remote_agent

        def browser_remote_agent(
            cfg: Any,
            *,
            allow_unattended: bool = False,
            printer: Callable[[str], None] = print,
            event_sink: Callable[[str, dict[str, Any]], None] | None = None,
        ) -> int:
            del printer, event_sink

            def dashboard_event(status: str, payload: dict[str, Any]) -> None:
                emit("remote", status=status, **payload)
                if status in {"ready", "idle"}:
                    emit("state", state="listening", label="Awaiting encrypted directives")
                elif status in {"task_received", "task_started"}:
                    emit("state", state="working", label="Executing remote directive")
                elif status == "task_result":
                    emit(
                        "state",
                        state="success" if payload.get("ok") else "warning",
                        label="Remote directive complete" if payload.get("ok") else "Remote directive ended",
                    )
                elif status == "task_error":
                    emit("state", state="warning", label=str(payload.get("message", "Remote task issue"))[:120])
                elif status == "stopped":
                    emit("state", state="offline", label="Remote agent stopped")

            return original_remote_agent(
                cfg,
                allow_unattended=allow_unattended,
                printer=print,
                event_sink=dashboard_event,
            )

        remote.run_remote_agent = browser_remote_agent

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
