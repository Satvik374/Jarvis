"""Interactive text console for Jarvis.

Type a task in plain English; Jarvis perceives the screen, reasons, and acts.
Special commands start with ':'.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import sys
import threading
import time

from pathlib import Path

from .config import load_config, Config
from .agent.brain import make_brain, BrainError
from .utils import logging as log
from .utils import voice
from .utils.logging import _c, _c256, _ARC, _COLORS
from .agent.loop import Agent, _IMG_EXTS
from . import scheduler


BANNER = r"""
  ┌───────────────────────────────────────────────────────────────┐
  │   _   _   ___     _____ ___                                   │
  │  _ | | /_\ | _ \ \ / /_ _/ __|   LOCAL AGENTIC DESKTOP ASSISTANT│
  │ | || |/ _ \|   /\ V / | |\__ \   perceive • think • act       │
  │  \__/ /_/ \_\_|_\ \_/ |___|___/   v5.0                        │
  └───────────────────────────────────────────────────────────────┘
"""


_SLASH_COMMANDS = (
    ("/enhance", "AI-rewrite a rough prompt, confirm, then run it"),
    ("/paste", "attach the clipboard image/screenshot (Ctrl+V works too)"),
    ("/remember", "[fact] - store a fact in permanent memory forever"),
    ("/memory", "list permanent memories and learned plans"),
    ("/help", "show all commands"),
    ("/voice", "voice-ONLY mode: talk instead of typing"),
    ("/macro", "watch & learn: record, list, or replay desktop workflows"),
    ("/secret", "Windows Credential Manager / DPAPI vault: set/get/list/migrate"),
    ("/see", "[question] - multimodal live screen & webcam visual perception"),
    ("/cam", "[snap|inspect] - physical webcam vision tools"),
    ("/daemon", "[status|list|enable|disable|tick] - proactive background daemon & event triggers"),
    ("/hud", "[show|hide|toggle|status] - global floating mini HUD & system-wide hotkeys"),
    ("/cron", "list/add/remove scheduled jobs"),
    ("/connect", "Gmail/Discord/WhatsApp connector status and test"),
    ("/remote", "list, remove, trust, or send tasks to paired devices"),
    ("/mcp", "list/add/remove MCP servers (extra tool connectors)"),
    ("/startup", "on|off - launch Jarvis when Windows starts"),
    ("/confirm", "on|off - confirm each action"),
    ("/vision", "on|off - send screenshots to the model"),
    ("/steps", "set max steps per task, e.g. /steps 20"),
    ("/config", "print the active configuration"),
    ("/quit", "exit"),
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _matches(typed: str) -> list[tuple[str, str]]:
    """Slash commands whose name starts with what's typed so far.
    Once a space is typed the command is chosen - hide the menu."""
    if " " in typed:
        return []
    return [c for c in _SLASH_COMMANDS if c[0].startswith(typed.lower())]


def _draw_menu(matches, col: int) -> None:
    """Draw (or clear, when ``matches`` is empty) the live command menu below
    the input line, then put the cursor back where the user is typing.
    Relative cursor moves only, so scrolling at the screen bottom stays safe."""
    n = len(matches or [])
    s = "\n\x1b[0J"                       # go below the input line, wipe stale menu
    for name, desc in (matches or []):
        s += f"  {_c(f'{name:<10}', 'cyan')} {_c(desc, 'grey')}\n"
    s += f"\x1b[{n + 1}A\x1b[{col}G"      # back up to the input line and column
    sys.stdout.write(s)
    sys.stdout.flush()


def _stdout_echo(s: str) -> None:
    sys.stdout.write(s)
    sys.stdout.flush()


def _clipboard_to_path() -> str | None:
    """The clipboard's image, as a file path the prompt pipeline understands.

    A pasted screenshot (Win+Shift+S / PrtScn) is saved to a temp PNG; an
    image FILE copied in Explorer resolves to its own path. None when the
    clipboard holds no image - text pastes are the terminal's job.
    """
    try:
        from PIL import ImageGrab  # type: ignore

        data = ImageGrab.grabclipboard()
    except Exception:
        return None
    if isinstance(data, list):                     # copied file(s) in Explorer
        for f in data:
            if str(f).lower().endswith(_IMG_EXTS):
                return str(f)
        return None
    if data is None:
        return None
    import tempfile
    path = Path(tempfile.gettempdir()) / f"jarvis_paste_{int(time.time() * 1000)}.png"
    try:
        data.save(path, format="PNG")
    except Exception:
        return None
    return str(path)


def _read_input(prompt: str) -> str:
    """Read one prompt. On Windows this is a character-level reader: typing
    '/' pops up a live-filtered command menu (like Claude Code), and a
    multi-line PASTE is absorbed as ONE input."""
    if sys.platform != "win32":
        return input(prompt)
    try:
        import msvcrt
    except ImportError:
        return input(prompt)
    return _char_input(prompt, msvcrt.kbhit, msvcrt.getwch)


# Shortest gap before a key counts as hand-typed rather than pasted. Pasted
# keys are already sitting in the console buffer (microseconds); a human is
# never below ~30ms, even at 150wpm.
_KEY_GAP = 0.02


def _char_input(prompt: str, kbhit, getwch, grace: float = 0.05,
                echo=None, menu=None) -> str:
    """Character-level line reader. ``kbhit``/``getwch``/``echo``/``menu``
    are injectable for tests.

    * '/' as the first character shows the slash-command menu instantly,
      filtered as you type; Tab completes the first match.
    * Enter submits only when the input buffer is idle afterwards AND the key
      was actually waited for - newlines inside a paste (including a trailing
      one) are kept, so a paste stays ONE prompt you can keep typing into.
    * Left/Right (and Ctrl+Left/Right by word), Home/End and Delete move and
      edit inside the line, so a typo mid-prompt does not mean retyping the
      tail of it.
    """
    echo = echo if echo is not None else _stdout_echo
    menu = menu if menu is not None else _draw_menu
    echo(prompt)
    vis_prompt = len(_ANSI_RE.sub("", prompt).rsplit("\n", 1)[-1])
    buf: list[str] = []
    pos = 0                        # cursor index into buf; edits happen here
    lines: list[str] = []
    pending: str | None = None     # one char read ahead past a '\r'
    menu_on = False

    def update_menu() -> None:
        nonlocal menu_on
        cur = "".join(buf)
        # The menu re-homes the cursor with an absolute column, so it has to
        # follow `pos` - using len(buf) would yank the cursor back to the end
        # of the line on every arrow press.
        col = (vis_prompt if not lines else 0) + pos + 1
        if not lines and cur.startswith("/"):
            menu(_matches(cur), col)
            menu_on = True
        elif menu_on:
            menu(None, col)
            menu_on = False

    def redraw_tail(blank: int = 0) -> None:
        """Reprint what sits right of the cursor, then park the cursor back on
        it. ``blank`` wipes the columns the tail no longer reaches after a
        delete.

        ponytail: steps in characters, not display columns, so a full-width
        CJK/emoji character mid-line under-steps. Walk by _vislen if that ever
        shows up in a real prompt.
        """
        tail = "".join(buf[pos:])
        if tail or blank:
            echo(tail + " " * blank + "\b" * (len(tail) + blank))

    def insert(text: str) -> None:
        nonlocal pos
        buf[pos:pos] = list(text)
        echo(text)
        pos += len(text)
        redraw_tail()

    def move_to(target: int) -> None:
        """Walk the cursor to an absolute buffer index, clamped to the line."""
        nonlocal pos
        target = max(0, min(len(buf), target))
        if target < pos:
            echo("\b" * (pos - target))
        elif target > pos:
            echo("".join(buf[pos:target]))
        pos = target

    def word_edge(step: int) -> int:
        """Index one word to the left (step -1) or right (step +1)."""
        peek = (lambda i: buf[i - 1]) if step < 0 else (lambda i: buf[i])
        more = (lambda i: i > 0) if step < 0 else (lambda i: i < len(buf))
        i = pos
        while more(i) and peek(i) == " ":       # skip the gap
            i += step
        while more(i) and peek(i) != " ":       # then the word itself
            i += step
        return i

    while True:
        if pending is not None:
            ch, pending = pending, None
            waited = 0.0                           # read ahead inside a paste
        else:
            t0 = time.monotonic()
            ch = getwch()
            waited = time.monotonic() - t0         # ~0 == it was already queued
        if ch == "\x03":                           # Ctrl+C
            if menu_on:
                menu(None, 1)
            raise KeyboardInterrupt
        if ch in "\r\n":
            if grace:
                time.sleep(grace)  # let the rest of a paste reach the buffer
            if ch == "\r" and kbhit():
                nxt = getwch()
                if nxt == "\n":                    # LF of a CRLF pair
                    if grace:
                        time.sleep(grace)
                else:
                    pending = nxt                  # more paste follows
            # A trailing newline at the END of a paste also leaves an idle
            # buffer, so "idle" alone submits the paste before the user can
            # type after it. A key that was already queued when we asked for
            # it (waited ~0) came from the paste, never from a keypress.
            typed = not grace or waited >= _KEY_GAP
            if typed and pending is None and not kbhit():   # a real Enter
                if menu_on:
                    menu(None, 1)
                echo("\n")
                lines.append("".join(buf))
                return "\n".join(lines)
            lines.append("".join(buf))             # pasted newline: keep going
            buf = []
            pos = 0
            echo("\n")
            update_menu()
            continue
        if ch == "\x08":                           # backspace
            if pos > 0:
                del buf[pos - 1]
                pos -= 1
                echo("\b")
                redraw_tail(1)
            update_menu()
            continue
        if ch == "\t":
            cur = "".join(buf)
            if not lines and cur.startswith("/") and " " not in cur:
                m = _matches(cur)
                if m:
                    move_to(len(buf))              # complete at the end
                    insert(m[0][0][len(cur):] + " ")
            update_menu()
            continue
        if ch == "\x16":                           # Ctrl+V: image clipboard
            path = _clipboard_to_path()
            if path:
                insert(f'"{path}" ')
            update_menu()
            continue
        if ch in ("\x00", "\xe0"):                 # extended key: two chars
            code = getwch()
            if code == "K":                        # left
                move_to(pos - 1)
            elif code == "M":                      # right
                move_to(pos + 1)
            elif code == "s":                      # Ctrl+left: word back
                move_to(word_edge(-1))
            elif code == "t":                      # Ctrl+right: word forward
                move_to(word_edge(1))
            elif code == "G":                      # Home
                move_to(0)
            elif code == "O":                      # End
                move_to(len(buf))
            elif code == "S":                      # Delete
                if pos < len(buf):
                    del buf[pos]
                    redraw_tail(1)
            # Up/Down and the F-keys have nothing to do here (no history yet).
            update_menu()
            continue
        insert(ch)
        update_menu()


def _greeting() -> str:
    """Time-aware JARVIS greeting for every start-up."""
    h = datetime.datetime.now().hour
    part = "morning" if 5 <= h < 12 else "afternoon" if h < 17 else "evening"
    return (f"Good {part}, sir. JARVIS at your service - all systems online. "
            "What shall we do today?")


def _persist_voice(enabled: bool) -> None:
    """Remember voice mode across sessions: rewrite the one ``voice_enabled``
    line in config.yaml (regex, not yaml.dump, so the file's comments survive)."""
    from . import config as _config
    try:
        text = _config.CONFIG_PATH.read_text(encoding="utf-8")
        val = f"voice_enabled: {str(enabled).lower()}"
        new, n = re.subn(r"(?m)^voice_enabled:.*$", val, text)
        if not n:
            new = text.rstrip("\n") + "\n" + val + "\n"
        _config.CONFIG_PATH.write_text(new, encoding="utf-8")
    except Exception as exc:
        log.warn(f"couldn't save voice preference: {exc}")


_STARTUP_BAT = Path(os.environ.get("APPDATA", "")) / (
    "Microsoft/Windows/Start Menu/Programs/Startup/jarvis.bat")


def _startup(on: bool) -> None:
    """':startup on|off' - launch Jarvis automatically at Windows login."""
    try:
        if on:
            root = Path(__file__).resolve().parent.parent
            _STARTUP_BAT.write_text(
                f'@echo off\ntitle JARVIS\ncd /d "{root}"\n'
                f'"{sys.executable}" run.py\n', encoding="utf-8")
            log.ok(f"Jarvis will greet you at every Windows start-up "
                   f"({_STARTUP_BAT})")
        else:
            _STARTUP_BAT.unlink(missing_ok=True)
            log.ok("Windows start-up launch removed.")
    except OSError as exc:
        log.warn(f"couldn't update the Startup folder: {exc}")


def _preflight(cfg: Config) -> None:
    """Warn early about the most common setup gaps, with fixes."""
    if cfg.brain.backend == "ollama":
        try:
            import requests  # type: ignore

            requests.get(f"{cfg.brain.base_url}/api/tags", timeout=3)
        except Exception:
            log.warn(f"Ollama not reachable at {cfg.brain.base_url}.")
            log.warn("  1) install: https://ollama.com/download")
            log.warn(f"  2) pull the model:  ollama pull {cfg.brain.model}")
            log.warn("  3) it serves automatically; then restart Jarvis.")


def _banner() -> str:
    """The ASCII art with a light->deep cyan gradient, line by line."""
    lines = BANNER.splitlines()
    shades = [_ARC[min(i, len(_ARC) - 1)] for i in range(len(lines))]
    return "\n".join(_c256(ln, n) for ln, n in zip(lines, shades))


def _status_bar(cfg: Config) -> str:
    def dot(on: bool) -> str:
        return _c("● on", "green") if on else _c("○ off", "grey")
    sep = _c(" │ ", "grey")
    return (f"  {_c('BRAIN', 'dim')} {_c(cfg.brain.backend, 'cyan')}:{cfg.brain.model}" + sep +
            f"{_c('VISION', 'dim')} {dot(cfg.brain.use_vision)}" + sep +
            f"{_c('UIA', 'dim')} {dot(cfg.perception.use_uia)}" + sep +
            f"{_c('OCR', 'dim')} {dot(cfg.perception.use_ocr)}" + sep +
            f"{_c('VOICE', 'dim')} {dot(cfg.voice_enabled)}" + sep +
            f"{_c('STEPS', 'dim')} {cfg.safety.max_steps}")


def repl(cfg: Config | None = None) -> int:
    cfg = cfg or load_config()
    print(_banner())
    print(_status_bar(cfg))
    log.rule()
    _preflight(cfg)

    try:
        brain = make_brain(cfg.brain)
    except BrainError as exc:
        log.error(str(exc))
        return 1
    agent = Agent(brain, cfg)
    voice.configure(brain, cfg.voice)

    # Refresh cloud credentials while the user is reading the greeting. This
    # keeps first-command latency off the interactive critical path; failures
    # remain silent here and are reported normally by the real request.
    threading.Thread(
        target=lambda: _warm_brain(brain),
        daemon=True,
        name="brain-warmup",
    ).start()

    # Cron: a background thread fires scheduled jobs through the same agent.
    # Every job run holds the desktop lock so it never fights a foreground task.
    def _cron_runner(command: str) -> None:
        log.rule(f"cron: {command[:50]}", "magenta")
        with scheduler.desktop():
            result = agent.run(command)
        log.jarvis(f"[scheduled] {result}")
        try:      # toast so the result is seen even away from the console
            from .tools import system as system_tools
            system_tools.notify(result[:200], title="JARVIS · scheduled task")
        except Exception:
            pass
        try:
            voice.speak(result, wait=True)
        except Exception:
            pass

    sched = scheduler.Scheduler(
        Path(__file__).resolve().parent.parent / "cron_jobs.json",
        runner=_cron_runner)
    scheduler.set_default(sched)
    sched.start()

    # Proactive Background Daemon: monitor hardware, OS events, and routines
    from . import daemon
    daemon.start_daemon(cfg=cfg, task_runner=_cron_runner)

    # Floating Mini HUD: Always-On-Top global capsule overlay & hotkeys
    def _hud_task_runner(command: str) -> str:
        log.rule(f"HUD › {command[:60]}", "cyan")
        started = time.time()
        try:
            with scheduler.desktop():
                result = agent.run(command, asker=_typed_asker)
        except Exception as exc:
            log.error(f"HUD execution error: {exc}")
            result = f"Error: {exc}"
        log.jarvis(result)
        try:
            voice.speak(result, wait=False)
        except Exception:
            pass
        log.rule(f"done in {time.time() - started:.1f}s")
        return result

    from . import hud
    if getattr(cfg, "hud", None) and cfg.hud.enabled:
        hud.start_hud(cfg=cfg, task_runner=_hud_task_runner)

    # MCP: warm up any configured connectors in the background so their tools
    # are ready by the time the user gives a task.
    from . import mcp
    mcp.get_manager().connect_all(background=True)

    # Account connectors: open Gmail's IMAP session and pre-load the unread
    # list now, so the first "any new mail?" answers from cache instantly.
    from .tools import connectors
    connectors.warm(background=True)

    greeting = _greeting()
    log.jarvis(f"{greeting} (':help' for commands, ':voice on' to talk, "
               "':wake' for hands-free, ':cron' to schedule, ':quit' to exit)")
    # Jarvis always speaks, in every mode; voice mode only adds the mic (STT).
    voice.speak(greeting, wait=cfg.voice_enabled or cfg.wake_enabled)
    prompt = f"\n{_COLORS['cyan']}╭─{_COLORS['reset']}{_COLORS['bold']} {_c('you', 'cyan')} {_COLORS['dim']}›{_COLORS['reset']} "

    # Launched with --voice / voice_enabled: greeted aloud above, go straight
    # to voice-only mode (the greeting replaces the loop's own announcement).
    if cfg.voice_enabled:
        try:
            _voice_loop(agent, cfg, announce=False)
        except KeyboardInterrupt:
            print()
        cfg.voice_enabled = False
        _persist_voice(False)
        log.ok("voice mode off - typed prompt, replies still spoken. "
               "(':voice on' to resume)")

    # Launched with --wake / wake_enabled: go straight into hands-free mode -
    # say "Hey Jarvis", get asked what you want, give the task, get a spoken
    # summary, then it's back to listening for the wake word - repeat.
    if cfg.wake_enabled:
        try:
            _wake_loop(agent, cfg, announce=False)
        except KeyboardInterrupt:
            print()
        cfg.wake_enabled = False
        log.ok('hands-free mode off; back to the typed prompt. '
               "(':wake' to resume)")

    while True:
        try:
            task = _read_input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not task:
            continue
        if task.startswith("/"):
            body = task[1:].strip()
            head = body.split(None, 1)[0].lower() if body else ""
            if head == "enhance":
                rest = body.split(None, 1)[1] if " " in body else ""
                task = _enhance(rest, agent)
                if not task:
                    continue
                # fall through: run the enhanced prompt as a normal task
            elif head == "paste":
                rest = body.split(None, 1)[1] if " " in body else ""
                task = _paste_task(rest)
                if not task:
                    continue
                # fall through: run the prompt with the image path attached
            else:
                task = ":" + body    # /help, /voice, /quit... mirror ':' commands
        if task.startswith(":"):
            c = task[1:].strip().lower()
            if c == "wake":
                try:
                    _wake_loop(agent, cfg)
                except KeyboardInterrupt:
                    print()
                    log.ok("hands-free mode off; back to the prompt.")
                continue
            if c.startswith("voice"):
                # Voice mode is voice-ONLY: entering it replaces the typed
                # prompt until Ctrl+C / "exit voice mode".
                if c.endswith("off"):
                    _persist_voice(False)
                    log.ok("voice is already off (typed prompt).")
                    continue
                cfg.voice_enabled = True
                _persist_voice(True)     # stays on across sessions
                try:
                    _voice_loop(agent, cfg)
                except KeyboardInterrupt:
                    print()
                cfg.voice_enabled = False
                _persist_voice(False)    # user chose the typed prompt again
                log.ok("voice mode off - typed prompt, replies still spoken. "
                       "(':voice on' to resume)")
                continue
            if _command(task, cfg):
                break
            continue
        log.rule(task[:60], "blue")
        started = time.time()
        try:
            with scheduler.desktop():      # serialize with any cron job
                result = agent.run(task, asker=_typed_asker)
        except KeyboardInterrupt:
            log.warn("interrupted; back to prompt.")
            continue
        except Exception as exc:
            log.error(f"unexpected error: {exc}")
            continue
        log.jarvis(result)
        voice.speak(result)             # async: keep the prompt responsive
        log.rule(f"done in {time.time() - started:.1f}s")
    sched.stop()
    scheduler.set_default(None)
    from . import daemon, hud
    hud.stop_hud()
    daemon.stop_daemon()
    mcp.get_manager().close_all()
    log.jarvis("Goodbye.")
    voice.speak("Goodbye, sir.", wait=True)   # sync: the process is exiting
    return 0


def _warm_brain(brain) -> None:
    try:
        brain.warmup()
    except Exception:
        pass


def _enhance(prompt: str, agent: Agent) -> str | None:
    """/enhance: rewrite a rough prompt with the SAME brain Jarvis runs on,
    show the result, and run it on confirmation. Returns the prompt to run,
    or None to go back to the input line."""
    if not prompt:
        log.warn("usage: /enhance <your rough prompt>")
        return None
    system = (
        "You are a prompt enhancer for JARVIS, an AI assistant that controls "
        "the user's Windows desktop. Rewrite the user's rough prompt into a "
        "clear, specific, unambiguous instruction. Keep the original intent "
        "and any file paths EXACTLY as written; add obvious missing details; "
        "stay concise (1-3 sentences). Do not invent requirements.\n"
        'Reply with ONE JSON object and nothing else: {"prompt": "<improved prompt>"}'
    )
    try:
        with log.spinner("enhancing"):
            raw = agent.brain.complete(system,
                                       [{"role": "user", "content": prompt}])
    except Exception as exc:
        log.error(f"enhance failed: {exc}")
        return None
    from .agent.prompts import _extract_json
    obj = _extract_json(raw)
    improved = (obj.get("prompt") if isinstance(obj, dict) else None) or raw
    improved = str(improved).strip()
    if not improved:
        log.warn("the model returned nothing; running your original prompt.")
        return prompt
    log.rule("enhanced prompt", "cyan")
    print(f"  {improved}")
    log.rule()
    try:
        ans = input(_c("  run it? [Y/n] › ", "cyan")).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    return improved if ans in {"", "y", "yes"} else None


def _paste_task(rest: str) -> str | None:
    """/paste: turn the clipboard image into a prompt with its path attached.
    Returns the task to run, or None to go back to the input line."""
    path = _clipboard_to_path()
    if not path:
        log.warn("no image in the clipboard - take a screenshot (Win+Shift+S) "
                 "or copy an image file first.")
        return None
    log.ok(f"attached {path}")
    if not rest:
        try:
            rest = _read_input(_c("  what should I do with it? › ", "cyan")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if not rest:
            return None
    return f'{rest} "{path}"'


def _typed_asker(question: str) -> str | None:
    """Mid-task question -> typed answer at the console (empty = no answer).
    Multi-line pastes are absorbed as one answer, same as the main prompt."""
    log.jarvis(question)
    voice.speak(question)               # spoken too; answer is still typed
    try:
        return _read_input(_c("  answer › ", "cyan")).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None


def _voice_asker_for(agent: Agent):
    """Mid-task question -> spoken answer, for the voice/hands-free loops."""
    def _asker(question: str) -> str | None:
        voice.speak(question, wait=True)
        log.info("listening for your answer...")
        wav = voice.listen(start_timeout=15.0)
        if not wav:
            return None
        with log.spinner("transcribing"):
            return voice.transcribe(wav, agent.brain)
    return _asker


_VOICE_EXIT_PHRASES = {"exit voice mode", "stop voice mode", "voice off",
                       "stop listening", "goodbye jarvis"}


def _voice_loop(agent: Agent, cfg: Config, announce: bool = True) -> None:
    """Voice-ONLY mode: Full-duplex with real-time interruption (Barge-in).
    Exit with Ctrl+C or by saying one of the exit phrases."""
    duplex_on = getattr(cfg.voice, "full_duplex", True)
    duplex_label = " (Full-Duplex Barge-in ON)" if duplex_on else ""
    log.ok(f'voice mode ON{duplex_label} - just speak. Say "exit voice mode" or press Ctrl+C to go back.')
    if announce:
        voice.speak("Voice mode on. I am listening.", wait=True)

    pending_wav: bytes | None = None
    while True:
        if pending_wav:
            wav = pending_wav
            pending_wav = None
        else:
            log.info("listening...")
            wav = voice.listen(start_timeout=30.0)

        if not wav:
            continue                      # silence - keep waiting
        with log.spinner("transcribing"):
            task = voice.transcribe(wav, agent.brain)
        if not task:
            voice.speak("Sorry, I couldn't understand that.", wait=True)
            continue
        log.info(f'heard: "{task}"')
        if task.strip().lower().rstrip(".!,") in _VOICE_EXIT_PHRASES:
            voice.speak("Voice mode off.", wait=True)
            return
        log.rule(task[:60], "blue")
        started = time.time()
        try:
            with scheduler.desktop():     # serialize with any cron job
                result = agent.run(task, asker=_voice_asker_for(agent))
        except KeyboardInterrupt:
            raise                         # exit voice mode entirely
        except Exception as exc:
            log.error(f"unexpected error: {exc}")
            voice.speak("Something went wrong with that task.", wait=True)
            continue
        log.jarvis(result)
        log.rule(f"done in {time.time() - started:.1f}s")

        if duplex_on:
            # Full-duplex speak & listen: speak reply and immediately catch any user barge-in
            interrupted_wav, was_interrupted = voice.speak_and_listen(
                result,
                start_timeout=4.0,
                full_duplex=True,
            )
            if interrupted_wav:
                pending_wav = interrupted_wav
        else:
            voice.speak(result, wait=True)


def _wake_loop(agent: Agent, cfg: Config, announce: bool = True) -> None:
    """Hands-free mode: wait for "hey jarvis", ask what's needed, listen,
    act, speak a summary - then go straight back to listening for the wake
    word. Ctrl+C (handled by the caller) exits back to the typed prompt."""
    log.ok('hands-free mode ON - say "Hey Jarvis" to give a command '
           "(Ctrl+C to exit)")
    if announce:
        voice.speak("Hands free mode on. Say hey jarvis when you need me.",
                    wait=True)
    while True:
        log.info('waiting for "Hey Jarvis"...')
        if not voice.wait_for_wake():
            log.warn("wake-word listener unavailable; leaving hands-free mode.")
            return
        # sync so Jarvis's own voice never bleeds into the mic it's about to open
        voice.speak("Yes? What would you like me to do?", wait=True)
        log.info("listening... speak your command")
        wav = voice.listen(start_timeout=8.0)
        if not wav:
            voice.speak("I didn't catch that. Say hey jarvis to try again.")
            continue
        with log.spinner("transcribing"):
            task = voice.transcribe(wav, agent.brain)
        if not task:
            voice.speak("Sorry, I couldn't understand that. Say hey jarvis to try again.")
            continue
        log.info(f'heard: "{task}"')
        log.rule(task[:60], "blue")
        started = time.time()
        try:
            with scheduler.desktop():        # serialize with any cron job
                result = agent.run(task, asker=_voice_asker_for(agent))
        except KeyboardInterrupt:
            raise                            # exit hands-free mode entirely
        except Exception as exc:
            log.error(f"unexpected error: {exc}")
            voice.speak("Something went wrong with that task.", wait=True)
            continue
        log.jarvis(result)
        # wait=True: the reply finishes before we go back to listening for
        # the wake word, so Jarvis's own summary can't trigger a false wake.
        voice.speak(result, wait=True)
        log.rule(f"done in {time.time() - started:.1f}s")


def _command(cmd: str, cfg: Config) -> bool:
    """Handle ':' commands. Returns True if the REPL should exit."""
    c = cmd[1:].strip().lower()
    if c in {"quit", "exit", "q"}:
        return True
    if c in {"help", "h", "?"}:
        log.rule("commands", "cyan")
        for cmd, desc in _SLASH_COMMANDS + (
            ("(image input)", "drag & drop an image file into your prompt to attach it"),
        ):
            print(f"  {_c(f'{cmd:<16}', 'cyan')} {_c(desc, 'grey')}")
        print(f"  {_c('type / for the live menu; the : prefix works too', 'grey')}")
        log.rule()
    elif c.startswith("startup"):
        arg = c.split()[-1]
        if arg in {"on", "off"}:
            _startup(arg == "on")
        else:
            log.warn("usage: :startup on|off")
    elif c.startswith("confirm"):
        cfg.safety.confirm_each_action = c.endswith("on")
        log.ok(f"confirm_each_action = {cfg.safety.confirm_each_action}")
    elif c.startswith("vision"):
        cfg.brain.use_vision = c.endswith("on")
        log.ok(f"use_vision = {cfg.brain.use_vision}")
    elif c.startswith("steps"):
        try:
            cfg.safety.max_steps = int(c.split()[1])
            log.ok(f"max_steps = {cfg.safety.max_steps}")
        except (IndexError, ValueError):
            log.warn("usage: :steps 20")
    elif c == "config":
        import json
        print(json.dumps(cfg.as_dict(), indent=2, default=str))
    elif c.startswith("remember") or c == "remember":
        parts = cmd.strip().split(maxsplit=1)
        fact = parts[1].strip() if len(parts) > 1 else ""
        if fact:
            from .agent.memory import remember_fact
            msg = remember_fact(fact=fact, category="user")
            log.ok(msg)
        else:
            log.warn("usage: /remember <fact to store forever>")
    elif c == "memory" or c.startswith("memory "):
        parts = cmd.strip().split(maxsplit=2)
        sub = parts[1].lower() if len(parts) > 1 else ""
        arg = parts[2].strip() if len(parts) > 2 else ""

        from .memory.manager import get_memory_manager
        mgr = get_memory_manager()

        if sub in {"search", "query", "find"}:
            if not arg:
                log.warn("usage: :memory search <topic or question>")
            else:
                results = mgr.search_semantic(arg, top_k=6)
                log.rule(f"SEMANTIC MEMORY SEARCH: '{arg}'", "cyan")
                if results:
                    for rec, score in results:
                        score_color = "green" if score > 0.6 else "yellow"
                        print(f"  {_c(f'[{score:.2f}]', score_color)} {_c(f'[{rec.category}]', 'cyan')} {rec.content}")
                else:
                    print(_c("  (No relevant memory found)", "grey"))
                log.rule()

        elif sub == "graph":
            log.rule("KNOWLEDGE GRAPH (ENTITIES & RELATIONS)", "cyan")
            if arg:
                subgraph = mgr.query_graph(arg)
                relations = subgraph.get("relations", [])
                if relations:
                    for rel in relations:
                        ctx = f" {_c(f'({rel.context})', 'grey')}" if rel.context else ""
                        print(f"  {_c(rel.source_name, 'cyan')} ──[{_c(rel.relation_type, 'yellow')}]──▶ {_c(rel.target_name, 'green')}{ctx}")
                else:
                    print(_c(f"  (No graph connections found for entity '{arg}')", "grey"))
            else:
                triplets = mgr.knowledge_graph.get_all_triplets()
                if triplets:
                    for s, r, t, ctx in triplets:
                        ctx_str = f" {_c(f'({ctx})', 'grey')}" if ctx else ""
                        print(f"  {_c(s, 'cyan')} ──[{_c(r, 'yellow')}]──▶ {_c(t, 'green')}{ctx_str}")
                else:
                    print(_c("  (No knowledge graph triplets stored yet)", "grey"))
            log.rule()

        elif sub == "sync":
            f_count, p_count = mgr.sync_from_file()
            log.ok(f"Synchronized memory database: {f_count} facts, {p_count} learned plans.")

        else:
            stats = mgr.get_stats()
            log.rule("JARVIS LONG-TERM MEMORY (VECTOR + GRAPH RAG)", "cyan")
            print(f"  {_c('• Vectors / Embeddings:', 'yellow')} {stats['total_vectors']} records ({stats['facts_count']} facts, {stats['learned_plans_count']} plans)")
            print(f"  {_c('• Knowledge Graph:    ', 'yellow')} {stats['graph_entities']} entities, {stats['graph_relations']} relations")
            print(f"  {_c('• SQLite DB:          ', 'grey')} {stats['db_path']}")
            print(f"  {_c('• Commands:           ', 'grey')} :memory search <q> | :memory graph [ent] | :memory sync\n")

            from .agent.memory import parse_memory_text, get_default_memory_path
            p = get_default_memory_path()
            if p.exists():
                text = p.read_text(encoding="utf-8")
                facts, plans = parse_memory_text(text)
                log.rule("PERMANENT FACTS", "cyan")
                if facts:
                    for f in facts:
                        print(f"  {_c('•', 'cyan')} {f}")
                else:
                    print(_c("  (No permanent memories stored)", "grey"))
                log.rule("LEARNED TASK PLANS", "cyan")
                if plans:
                    for plan in plans[:5]:
                        print(_c(plan, "grey"))
                    if len(plans) > 5:
                        print(_c(f"  ... and {len(plans) - 5} more learned plans (search with :memory search <task>)", "grey"))
                else:
                    print(_c("  (No learned task plans)", "grey"))
                log.rule()

    elif c == "browser" or c.startswith("browser "):
        _browser_command(cmd, cfg)
    elif c == "voice" or c.startswith("voice "):
        _voice_command(cmd, cfg)
    elif c == "macro" or c.startswith("macro "):
        _macro_command(cmd, cfg)
    elif c == "secret" or c.startswith("secret ") or c == "vault" or c.startswith("vault "):
        _secret_command(cmd, cfg)
    elif c == "see" or c.startswith("see "):
        _see_command(cmd, cfg)
    elif c == "cam" or c.startswith("cam ") or c == "camera" or c.startswith("camera "):
        _cam_command(cmd, cfg)
    elif c == "daemon" or c.startswith("daemon "):
        _daemon_command(cmd)
    elif c == "hud" or c.startswith("hud "):
        _hud_command(cmd, cfg)
    elif c == "cron" or c.startswith("cron "):
        _cron_command(cmd)
    elif c == "mcp" or c.startswith("mcp "):
        _mcp_command(cmd)
    elif c == "connect" or c.startswith("connect "):
        _connect_command(cmd)
    elif c == "remote" or c.startswith("remote "):
        _remote_command(cmd, cfg)
    else:
        log.warn(f"unknown command '{cmd}' (':help' for the list)")
    return False


def _macro_command(raw: str, cfg: Config) -> None:
    """Handle ':macro [record <name> [desc] | stop | play <name> [speed] | list | show <name> | delete <name>]'."""
    parts = raw.strip().split(maxsplit=2)
    sub = parts[1].lower() if len(parts) > 1 else "list"
    arg = parts[2].strip() if len(parts) > 2 else ""

    from .macro import get_macro_manager, MacroPlayer
    from .macro.recorder import get_macro_recorder

    mgr = get_macro_manager()
    rec = get_macro_recorder(mgr)

    if sub in {"record", "start", "rec"}:
        if not arg:
            log.warn("usage: :macro record <name> [optional description]")
            return
        arg_parts = arg.split(maxsplit=1)
        name = arg_parts[0]
        desc = arg_parts[1] if len(arg_parts) > 1 else ""
        rec.start_recording(name=name, description=desc)
        log.rule(f"WATCH & LEARN MACRO RECORDER: '{name}'", "yellow")
        print("  • Jarvis is now watching your mouse clicks, keyboard typing, and window focus.")
        print("  • Perform your desired actions across any app or desktop window.")
        print("  • When finished, run ':macro stop' to save and learn this workflow.\n")

    elif sub in {"stop", "end", "save"}:
        if not rec.is_recording:
            log.warn("Macro recorder is not active. Use ':macro record <name>' first.")
            return
        log.info("Synthesizing recorded actions into optimized macro steps...")
        macro = rec.stop_recording(save_to_memory=True)
        log.rule(f"LEARNED MACRO: {macro.name}", "green")
        print(macro.format_plan())
        log.rule()

    elif sub in {"play", "run", "exec"}:
        if not arg:
            log.warn("usage: :macro play <name> [speed multiplier (e.g. 1.5)]")
            return
        arg_parts = arg.split(maxsplit=1)
        name = arg_parts[0]
        speed = 1.0
        if len(arg_parts) > 1:
            try:
                speed = float(arg_parts[1])
            except ValueError:
                pass

        player = MacroPlayer(mgr)
        res = player.play(name, speed=speed)
        if not res.get("ok"):
            log.error(res.get("message", "Playback failed."))

    elif sub in {"show", "view", "info"}:
        if not arg:
            log.warn("usage: :macro show <name>")
            return
        macro = mgr.load_macro(arg)
        if not macro:
            log.warn(f"Macro '{arg}' not found.")
            return
        log.rule(f"MACRO: {macro.name}", "cyan")
        print(macro.format_plan())
        log.rule()

    elif sub in {"delete", "remove", "rm"}:
        if not arg:
            log.warn("usage: :macro delete <name>")
            return
        ok = mgr.delete_macro(arg)
        if ok:
            log.ok(f"Macro '{arg}' deleted.")
        else:
            log.warn(f"Macro '{arg}' not found.")

    else:  # list
        macros = mgr.list_macros()
        log.rule("SAVED WORKFLOW MACROS (WATCH & LEARN)", "cyan")
        if not macros:
            print(_c("  (No macros recorded yet. Use ':macro record <name>' to record one)", "grey"))
        else:
            for m in macros:
                apps = f" [{', '.join(m.target_apps)}]" if m.target_apps else ""
                print(f"  {_c('• ' + m.name, 'yellow')}{_c(apps, 'cyan')} ({len(m.steps)} steps) - {_c(m.description or 'Custom Macro', 'grey')}")
        print(f"\n  {_c('Commands:', 'grey')} :macro record <name> | :macro stop | :macro play <name> [speed] | :macro show <name>\n")
        log.rule()


def _secret_command(raw: str, cfg: Config) -> None:
    """Handle ':secret [list | set <key> <val> [credman|dpapi] | get <key> | delete <key> | migrate]'."""
    parts = raw.strip().split(maxsplit=3)
    sub = parts[1].lower() if len(parts) > 1 else "list"

    from .security import get_credential_vault
    vault = get_credential_vault()

    if sub in {"set", "add"}:
        if len(parts) < 4:
            log.warn("usage: :secret set <key> <value> [credman|dpapi|all]")
            return
        key = parts[2].strip()
        val = parts[3].strip()
        backend = "credman"
        if " " in val:
            v_parts = val.rsplit(maxsplit=1)
            if v_parts[-1].lower() in {"credman", "dpapi", "all"}:
                val = v_parts[0]
                backend = v_parts[1].lower()
        ok = vault.set_secret(key, val, backend=backend)
        if ok:
            log.ok(f"Stored secret '{key}' in Windows Vault ({backend}): {vault.mask_secret(val)}")
        else:
            log.error(f"Failed to store secret '{key}'.")

    elif sub in {"get", "show"}:
        if len(parts) < 3:
            log.warn("usage: :secret get <key>")
            return
        key = parts[2].strip()
        val = vault.get_secret(key)
        if val:
            log.rule(f"SECRET: {key}", "cyan")
            print(f"  • Key:     {key}")
            print(f"  • Masked:  {vault.mask_secret(val)}")
            print(f"  • Length:  {len(val)} characters")
            log.rule()
        else:
            log.warn(f"Secret '{key}' not found in vault.")

    elif sub in {"del", "delete", "rm", "remove"}:
        if len(parts) < 3:
            log.warn("usage: :secret delete <key>")
            return
        key = parts[2].strip()
        ok = vault.delete_secret(key)
        if ok:
            log.ok(f"Deleted secret '{key}' from vault.")
        else:
            log.warn(f"Secret '{key}' not found.")

    elif sub in {"migrate", "import"}:
        migrated = vault.migrate_from_env()
        if migrated:
            log.ok(f"Migrated {len(migrated)} secret(s) to Windows Credential Manager: {', '.join(migrated)}")
        else:
            log.info("No unmanaged secrets found in .env or environment to migrate.")

    else:  # list / status
        secrets = vault.list_secrets()
        log.rule("WINDOWS CREDENTIAL MANAGER / DPAPI VAULT", "cyan")
        if not secrets:
            print(_c("  (No credentials stored in vault yet. Use ':secret set <key> <val>' or ':secret migrate')", "grey"))
        else:
            for s in secrets:
                b_color = "green" if s["backend"] == "credman" else "yellow" if "dpapi" in s["backend"] else "grey"
                print(f"  {_c('• ' + s['key'], 'cyan')} {_c(f'[{s['backend']}]', b_color)} : {s['masked']}")
        print(f"\n  {_c('Commands:', 'grey')} :secret set <key> <val> | :secret get <key> | :secret delete <key> | :secret migrate\n")
        log.rule()


def _see_command(raw: str, cfg: Config) -> None:
    """Handle ':see [optional question or instruction]'."""
    parts = raw.strip().split(maxsplit=1)
    prompt = parts[1].strip() if len(parts) > 1 else "Describe what you see on my screen and in my physical environment."

    from .perception import get_live_vision
    from .agent.brain import make_brain

    vision = get_live_vision()
    brain = make_brain(cfg.brain)

    log.rule("SEE WHAT I SEE (SCREEN + WEBCAM FUSION)", "cyan")
    with log.spinner("perceiving live screen and physical environment"):
        analysis = vision.analyze(source="both", prompt=prompt, brain=brain)
    print(f"\n{analysis}\n")
    log.rule()


def _cam_command(raw: str, cfg: Config) -> None:
    """Handle ':cam [snap | list | inspect <question>]'."""
    parts = raw.strip().split(maxsplit=2)
    sub = parts[1].lower() if len(parts) > 1 else "snap"
    arg = parts[2].strip() if len(parts) > 2 else ""

    from .perception import get_live_vision
    from .agent.brain import make_brain

    vision = get_live_vision()

    if sub in {"inspect", "ask", "see"}:
        prompt = arg or "Describe what is visible through the camera."
        brain = make_brain(cfg.brain)
        log.rule("LIVE WEBCAM PERCEPTION", "cyan")
        with log.spinner("analyzing physical camera feed"):
            analysis = vision.analyze(source="webcam", prompt=prompt, brain=brain)
        print(f"\n{analysis}\n")
        log.rule()

    elif sub in {"snap", "shot", "capture"}:
        cam = vision.capture_webcam()
        if cam:
            p = Path.home() / "Pictures" / f"jarvis_cam_{int(time.time())}.jpg"
            p.parent.mkdir(parents=True, exist_ok=True)
            cam.save(p)
            log.ok(f"Webcam snapshot saved: {p} ({cam.width}x{cam.height})")
        else:
            log.warn("Webcam is offline or unavailable.")

    else:
        log.rule("WEBCAM VISION COMMANDS", "cyan")
        print(f"  {_c('• :cam snap', 'yellow')}            - Take a snapshot and save to ~/Pictures")
        print(f"  {_c('• :cam inspect <prompt>', 'yellow')} - Visually analyze physical objects/scene via webcam")
        print(f"  {_c('• :see <prompt>', 'yellow')}         - Multimodal dual-fusion (Screen + Webcam)\n")
        log.rule()



def _voice_command(raw: str, cfg: Config) -> None:
    """Handle ':voice [duplex [on|off] | sensitivity <0.1-1.0> | interrupt | status]'."""
    parts = raw.strip().split(maxsplit=2)
    sub = parts[1].lower() if len(parts) > 1 else "status"
    arg = parts[2].strip() if len(parts) > 2 else ""

    if sub in {"duplex", "bargein", "barge-in"}:
        if arg in {"on", "1", "true", "yes"}:
            cfg.voice.full_duplex = True
            log.ok("Full-duplex voice with real-time barge-in: ON")
        elif arg in {"off", "0", "false", "no"}:
            cfg.voice.full_duplex = False
            log.ok("Full-duplex voice: OFF (half-duplex serialized mode)")
        else:
            log.info(f"Full-duplex voice is currently: {'ON' if cfg.voice.full_duplex else 'OFF'}")
    elif sub in {"sensitivity", "sens"}:
        if arg:
            try:
                val = float(arg)
                cfg.voice.barge_in_sensitivity = max(0.1, min(1.0, val))
                log.ok(f"Barge-in sensitivity set to: {cfg.voice.barge_in_sensitivity:.2f}")
            except ValueError:
                log.warn("usage: :voice sensitivity <0.1 to 1.0>")
        else:
            log.info(f"Current barge-in sensitivity: {cfg.voice.barge_in_sensitivity:.2f}")
    elif sub in {"interrupt", "stop", "quiet", "silence"}:
        was_speaking = voice.interrupt_speech()
        if was_speaking:
            log.ok("Interrupted active voice playback.")
        else:
            log.info("Voice was not actively speaking.")
    else:
        log.rule("VOICE CONFIGURATION & STATUS", "cyan")
        print(f"  • Speaking Now:         {'YES' if voice.is_speaking() else 'No'}")
        print(f"  • Full-Duplex Barge-in: {'ENABLED' if cfg.voice.full_duplex else 'Disabled'}")
        print(f"  • Barge-in Sensitivity: {cfg.voice.barge_in_sensitivity:.2f}")
        print(f"  • TTS Engine:           {cfg.voice.engine} ({cfg.voice.voice if cfg.voice.engine == 'gemini' else cfg.voice.local_voice})")
        print(f"  • Commands:             :voice duplex on|off | :voice sensitivity <val> | :voice interrupt")
        log.rule()



def _browser_command(raw: str, cfg: Config) -> None:
    """Handle ':browser [goto <url> | snap | click <target> | type <target> <text> | shot | close]'."""
    parts = raw.strip().split(maxsplit=2)
    sub = parts[1].lower() if len(parts) > 1 else "snap"
    arg = parts[2].strip() if len(parts) > 2 else ""

    from .browser_engine import get_browser_driver
    driver = get_browser_driver(cfg=cfg)

    if sub in {"goto", "open", "navigate"}:
        if not arg:
            log.warn("usage: :browser goto <url>")
            return
        log.info(f"Navigating to {arg}...")
        res = driver.navigate(arg)
        log.rule(f"BROWSER: {res.get('title')} ({res.get('url')})", "cyan")
        print(res.get("snapshot", ""))
        log.rule()

    elif sub in {"click"}:
        if not arg:
            log.warn("usage: :browser click <target (e.g. e1, #id, or text)>")
            return
        res = driver.click(arg)
        if res.get("ok"):
            log.ok(res.get("message", "Clicked."))
            print(res.get("snapshot", ""))
        else:
            log.error(res.get("message", "Click failed."))

    elif sub in {"type", "fill", "input"}:
        if not arg:
            log.warn("usage: :browser type <target> <text to type>")
            return
        subparts = arg.split(maxsplit=1)
        target = subparts[0]
        text = subparts[1] if len(subparts) > 1 else ""
        res = driver.type_text(target, text)
        if res.get("ok"):
            log.ok(res.get("message", "Typed."))
        else:
            log.error(res.get("message", "Type failed."))

    elif sub in {"shot", "screenshot"}:
        shot_path = driver.take_screenshot()
        log.ok(f"Browser screenshot saved to: {shot_path}")

    elif sub in {"close", "quit", "exit"}:
        driver.close()
        log.ok("Browser session closed.")

    else:  # snap or overview
        snap = driver.snapshot()
        log.rule(f"BROWSER SNAPSHOT: {snap.title} ({snap.url})", "cyan")
        print(snap.format_text())
        log.rule()


def _daemon_command(raw: str) -> None:
    """Handle ':daemon [status | list | remove <id> | enable <id> | disable <id> | tick]'."""
    from . import daemon
    d = daemon.get_daemon()
    body = raw[1:].strip()
    body = body[6:].strip() if body[:6].lower() == "daemon" else body

    if not body or body.lower() == "status":
        rules = d.list_rules()
        active_cnt = sum(1 for r in rules if r.enabled)
        log.rule("PROACTIVE BACKGROUND DAEMON STATUS", "cyan")
        print(f"  • Daemon:     {_c('ONLINE', 'green')}")
        print(f"  • Total Rules: {len(rules)} ({active_cnt} active)")
        print(f"  • Watchers:   Battery, Resource (CPU/RAM), File Drops, Window Focus, Daily Routines")
        print(f"  • Event Log:  {len(d.event_history)} recent event(s) recorded")
        print(f"\n  {_c('Commands:', 'grey')} :daemon list | :daemon enable <id> | :daemon disable <id> | :daemon tick\n")
        log.rule()
        return

    if body.lower() == "list":
        rules = d.list_rules()
        if not rules:
            log.info("No proactive rules configured.")
            return
        log.rule("PROACTIVE AUTOMATION RULES", "cyan")
        for r in rules:
            st = _c("ENABLED", "green") if r.enabled else _c("DISABLED", "yellow")
            print(f"  • [{_c(r.id, 'cyan')}] {r.name} ({st})")
            print(f"    Trigger: {_c(r.trigger_type.value, 'yellow')} ──▶ {_c(r.action_type.upper(), 'magenta')}: {r.action_target} (cooldown: {int(r.cooldown_seconds)}s)")
        print(f"\n  {_c('Manage:', 'grey')} :daemon enable <id> | :daemon disable <id> | :daemon remove <id>\n")
        log.rule()
        return

    if body.lower().startswith(("remove", "rm", "del")):
        parts = body.split()
        if len(parts) < 2:
            log.warn("usage: :daemon remove <rule_id>")
            return
        rid = parts[1]
        log.ok(f"Removed rule {rid}" if d.remove_rule(rid) else f"No rule with id {rid}")
        return

    if body.lower().startswith(("enable", "disable")):
        parts = body.split()
        if len(parts) < 2:
            log.warn(f"usage: :daemon {parts[0]} <rule_id>")
            return
        rid = parts[1]
        en = parts[0].lower() == "enable"
        log.ok(f"Rule {rid} {'enabled' if en else 'disabled'}" if d.enable_rule(rid, enabled=en) else f"No rule with id {rid}")
        return

    if body.lower() == "tick":
        events = d.tick()
        log.ok(f"Manual proactive daemon tick executed. Discovered {len(events)} event(s).")
        return

    log.warn("usage: :daemon [status | list | remove <id> | enable <id> | disable <id> | tick]")


def _hud_command(raw: str, cfg: Config) -> None:
    """Handle ':hud [status | show | hide | toggle]'."""
    parts = raw.strip().split(maxsplit=2)
    sub = parts[1].lower() if len(parts) > 1 else "status"

    from . import hud
    controller = hud.get_hud_controller(cfg=cfg)

    if sub == "show":
        controller.show_hud()
        log.ok("Floating Mini HUD is now visible.")
    elif sub == "hide":
        controller.hide_hud()
        log.ok("Floating Mini HUD is now hidden.")
    elif sub == "toggle":
        controller.toggle_hud()
        log.ok("Floating Mini HUD visibility toggled.")
    else:
        hud_cfg = getattr(cfg, "hud", None)
        log.rule("GLOBAL FLOATING MINI HUD & HOTKEYS", "cyan")
        print(f"  • HUD Enabled:    {_c('YES', 'green') if getattr(hud_cfg, 'enabled', True) else 'No'}")
        print(f"  • HUD Position:   {getattr(hud_cfg, 'position', 'bottom_right')}")
        print(f"  • Global Toggle:  {_c(getattr(hud_cfg, 'hotkey_toggle', 'ctrl+alt+j'), 'yellow')}")
        print(f"  • Push-To-Talk:   {_c(getattr(hud_cfg, 'hotkey_voice', 'ctrl+alt+v'), 'yellow')}")
        print(f"  • Live Vision:    {_c(getattr(hud_cfg, 'hotkey_vision', 'ctrl+alt+s'), 'yellow')}")
        print(f"  • Macro Record:   {_c(getattr(hud_cfg, 'hotkey_macro', 'ctrl+alt+r'), 'yellow')}")
        print(f"\n  {_c('Commands:', 'grey')} :hud show | :hud hide | :hud toggle | :hud status\n")
        log.rule()


def _cron_command(raw: str) -> None:
    """Handle ':cron [list | add <schedule> | <command> | remove <id>]'.

    Uses the raw (case-preserving) text so the scheduled command isn't
    lower-cased.
    """
    sched = scheduler.get_default()
    if sched is None:
        log.warn("scheduler is not running.")
        return
    body = raw[1:].strip()                    # drop leading ':'
    body = body[4:].strip() if body[:4].lower() == "cron" else body

    if not body or body.lower() == "list":
        jobs = sched.jobs()
        if not jobs:
            log.info("no scheduled jobs. Add one, e.g. "
                     ":cron add every 30 minutes | tell me the system status")
            return
        log.rule("cron jobs", "magenta")
        for j in jobs:
            print("  " + j.describe())
        return

    low = body.lower()
    if low.startswith(("remove", "rm", "del")):
        try:
            jid = int(body.split()[1])
        except (IndexError, ValueError):
            log.warn("usage: :cron remove <id>")
            return
        log.ok(f"removed job {jid}" if sched.remove(jid) else f"no job with id {jid}")
        return

    if low.startswith("add"):
        rest = body[3:].strip()
        if "|" not in rest:
            log.warn("usage: :cron add <schedule> | <command>   e.g. "
                     ":cron add daily at 08:00 | search the web for the news")
            return
        spec, command = rest.split("|", 1)
        try:
            job = sched.add(spec.strip(), command.strip())
        except scheduler.ScheduleError as exc:
            log.warn(str(exc))
            return
        log.ok(f"scheduled job {job.id}: {job.spec} -> {job.command!r}")
        return

    log.warn("usage: :cron [list | add <schedule> | <command> | remove <id>]")


_MCP_USAGE = (
    "usage: :mcp [list | tools <name> | add <name> <command> [args...] | "
    "add <name> {json} | remove <name> | enable <name> | disable <name>]\n"
    "  e.g. :mcp add filesystem npx -y @modelcontextprotocol/server-filesystem C:/Users\n"
    "       :mcp add github {\"command\":\"npx\",\"args\":[\"-y\",\"@modelcontextprotocol/"
    "server-github\"],\"env\":{\"GITHUB_TOKEN\":\"ghp_...\"}}")


def _connect_command(raw: str) -> None:
    """Handle ':connect [list | <service> [op] [args...]]'.

    ':connect' shows which accounts are wired up; ':connect gmail unread' runs
    a real call, so a setup problem surfaces here rather than mid-task.
    """
    from .tools import connectors
    body = raw[1:].strip()                    # drop leading ':'
    body = body[7:].strip() if body[:7].lower() == "connect" else body

    if not body or body.lower() == "list":
        log.info(connectors.status())
        log.info("test one with ':connect gmail unread' or "
                 "':connect discord messages #general'")
        return
    parts = body.split()
    service, op = parts[0], (parts[1] if len(parts) > 1 else "")
    rest = " ".join(parts[2:])
    # A test must hit the network, not the cache it is meant to validate.
    connectors.invalidate(service.lower())
    target = rest if rest.startswith("#") or rest.isdigit() else ""
    try:
        with log.spinner(f"asking {service}"):
            out = connectors.fetch(service, op or "unread",
                                   query="" if target else rest, target=target)
    except connectors.ConnectorError as exc:
        log.warn(str(exc))
        return
    log.ok(out)


def _remote_command(raw: str, cfg: Config) -> None:
    """Handle ``:remote`` commands without exposing pairing secrets in chat."""
    from . import remote
    try:
        output = remote.console_command(raw, cfg)
    except remote.RemoteError as exc:
        log.warn(str(exc))
        return
    log.info(output)


def _mcp_command(raw: str) -> None:
    """Handle ':mcp [list | tools <name> | add ... | remove/enable/disable <name>]'.

    Uses the raw (case-preserving) text so server names, commands, paths and
    tokens aren't lower-cased.
    """
    from . import mcp
    mgr = mcp.get_manager()
    body = raw[1:].strip()                    # drop leading ':'
    body = body[3:].strip() if body[:3].lower() == "mcp" else body
    low = body.lower()

    if not body or low == "list":
        log.info(mcp.manage({"op": "list"}))
        return
    if low.startswith("tools"):
        log.info(mcp.manage({"op": "tools", "name": body[5:].strip()}))
        return
    if low.split()[0] in {"remove", "rm", "del", "delete", "enable", "disable"}:
        verb, _, name = body.partition(" ")
        op = {"rm": "remove", "del": "remove", "delete": "remove"}.get(
            verb.lower(), verb.lower())
        if not name.strip():
            log.warn(f"usage: :mcp {verb} <name>")
            return
        log.ok(mcp.manage({"op": op, "name": name.strip()}))
        return
    if low.startswith("add"):
        rest = body[3:].strip()
        name, _, tail = rest.partition(" ")
        tail = tail.strip()
        if not name or not tail:
            log.warn(_MCP_USAGE)
            return
        spec: dict = {"op": "add", "name": name}
        if tail.startswith("{"):              # pasted claude_desktop-style JSON
            try:
                obj = json.loads(tail)
            except Exception as exc:
                log.warn(f"couldn't parse the JSON server spec: {exc}")
                return
            spec["command"] = obj.get("command", "")
            spec["args"] = obj.get("args", [])
            spec["env"] = obj.get("env", {})
        else:                                 # <command> [args...]
            parts = tail.split()
            spec["command"], spec["args"] = parts[0], parts[1:]
        with log.spinner(f"connecting to '{name}'"):
            result = mcp.manage(spec)
        log.ok(result)
        return
    log.warn(_MCP_USAGE)


if __name__ == "__main__":
    sys.exit(repl())
