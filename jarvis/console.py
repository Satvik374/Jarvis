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
    ("/wake", 'hands-free mode: say "Hey Jarvis" to command'),
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
               "':cron' to schedule, ':quit' to exit)")
    # Jarvis always speaks, in every mode; voice mode only adds the mic (STT).
    voice.speak(greeting, wait=cfg.voice_enabled)
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
    """Voice-ONLY mode: no typed prompt - listen, act, speak, repeat.
    Exit with Ctrl+C or by saying one of the exit phrases."""
    log.ok('voice mode ON - just speak. Say "exit voice mode" or press '
           "Ctrl+C to go back to typing.")
    if announce:
        voice.speak("Voice mode on. I am listening.", wait=True)
    while True:
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
        # wait=True: never let Jarvis's own speech bleed into the next listen
        voice.speak(result, wait=True)
        log.rule(f"done in {time.time() - started:.1f}s")


def _wake_loop(agent: Agent, cfg: Config) -> None:
    """Hands-free mode: wait for "hey jarvis", listen, act, speak - repeat.
    Ctrl+C (handled by the caller) exits back to the typed prompt."""
    log.ok('hands-free mode ON - say "Hey Jarvis" to give a command '
           "(Ctrl+C to exit)")
    voice.speak("Hands free mode on. Say hey jarvis when you need me.")
    while True:
        log.info('waiting for "Hey Jarvis"...')
        if not voice.wait_for_wake():
            log.warn("wake-word listener unavailable; leaving hands-free mode.")
            return
        voice.speak("Yes?", wait=True)      # sync so it never bleeds into the mic
        log.info("listening... speak your command")
        wav = voice.listen(start_timeout=8.0)
        if not wav:
            voice.speak("I didn't catch that.")
            continue
        with log.spinner("transcribing"):
            task = voice.transcribe(wav, agent.brain)
        if not task:
            voice.speak("Sorry, I couldn't understand that.")
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
            voice.speak("Something went wrong with that task.")
            continue
        log.jarvis(result)
        voice.speak(result)
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
        from .agent.memory import parse_memory_text, get_default_memory_path
        p = get_default_memory_path()
        if p.exists():
            text = p.read_text(encoding="utf-8")
            facts, plans = parse_memory_text(text)
            log.rule("PERMANENT MEMORIES", "cyan")
            if facts:
                for f in facts:
                    print(f"  {_c('•', 'cyan')} {f}")
            else:
                print(_c("  (No permanent memories stored)", "grey"))
            log.rule("LEARNED TASK PLANS", "cyan")
            if plans:
                for plan in plans:
                    print(_c(plan, "grey"))
            else:
                print(_c("  (No learned task plans)", "grey"))
            log.rule()
        else:
            log.info("No memory.txt file found yet.")
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
