#!/usr/bin/env python
"""Jarvis launcher.

Usage:
  python run.py                      # interactive console
  python run.py --browser            # interactive cyber UI in your browser
  python run.py "open notepad and type hello"   # run one task then exit
  python run.py --check              # environment / dependency check
  python run.py --backend ollama --model ornith:9b "..."
"""

from __future__ import annotations

# Configure DPI awareness for GUI automation on Windows (must be set before other imports)
import sys
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2) # 2 = PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

import argparse

from jarvis.config import load_config
from jarvis.utils import logging as log


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Jarvis - local agentic desktop assistant")
    parser.add_argument("task", nargs="*", help="a task to run once, then exit")
    parser.add_argument("--backend", help="override brain backend (ollama/openai/anthropic/llamacpp)")
    parser.add_argument("--model", help="override model name")
    parser.add_argument("--adapter", help="path to a trained LoRA adapter (hf backend)")
    parser.add_argument("--base-url", dest="base_url", help="override backend base URL")
    parser.add_argument("--vision", action="store_true", help="send screenshots to the model")
    parser.add_argument("--voice", action="store_true", help="speak replies aloud (and voice input in console)")
    parser.add_argument("--confirm", action="store_true", help="confirm each action")
    parser.add_argument("--steps", type=int, help="max steps per task")
    parser.add_argument("--check", action="store_true", help="run an environment check and exit")
    parser.add_argument(
        "--browser",
        action="store_true",
        help="start Jarvis in the local browser interface",
    )
    args = parser.parse_args(argv)

    cfg = load_config()
    if args.backend:
        cfg.brain.backend = args.backend
    if args.model:
        cfg.brain.model = args.model
    if args.adapter:
        cfg.brain.adapter_path = args.adapter
    if args.base_url:
        cfg.brain.base_url = args.base_url
    if args.vision:
        cfg.brain.use_vision = True
    if args.voice:
        cfg.voice_enabled = True
    if args.confirm:
        cfg.safety.confirm_each_action = True
    if args.steps:
        cfg.safety.max_steps = args.steps

    if args.check:
        return run_check(cfg)

    if args.browser:
        # The browser is a presentation layer over the regular terminal REPL.
        # Rebuild only the existing runtime overrides for the child session;
        # positional words are submitted as its first interactive task.
        child_args: list[str] = []
        for flag, value in (
            ("--backend", args.backend),
            ("--model", args.model),
            ("--adapter", args.adapter),
            ("--base-url", args.base_url),
        ):
            if value:
                child_args.extend((flag, value))
        if args.vision:
            child_args.append("--vision")
        if args.confirm:
            child_args.append("--confirm")
        if args.steps:
            child_args.extend(("--steps", str(args.steps)))

        from jarvis.browser import run_browser

        initial_task = " ".join(args.task).strip() or None
        return run_browser(child_args=child_args, initial_task=initial_task)

    if args.task:
        from jarvis.agent.brain import make_brain, BrainError
        from jarvis.agent.loop import Agent
        try:
            agent = Agent(make_brain(cfg.brain), cfg)
        except BrainError as exc:
            log.error(str(exc))
            return 1
        # Jarvis always speaks his replies (voice mode only adds the mic);
        # configure before running so mid-task questions are spoken too.
        from jarvis.utils import voice
        voice.configure(agent.brain, cfg.voice)
        # A real terminal can answer mid-task questions; a pipe cannot.
        import sys as _sys
        asker = None
        if _sys.stdin.isatty():
            from jarvis.console import _typed_asker
            asker = _typed_asker
        result = agent.run(" ".join(args.task), asker=asker)
        log.jarvis(result)
        voice.speak(result, wait=True)   # sync: don't exit mid-sentence
        return 0

    from jarvis.console import repl
    return repl(cfg)


def run_check(cfg) -> int:
    """Verify optional dependencies and the model backend are reachable."""
    log.info("Jarvis environment check\n")
    ok = True

    deps = [
        ("pyautogui", "mouse/keyboard control"),
        ("mss", "fast screenshots"),
        ("PIL", "image handling (Pillow)"),
        ("pygetwindow", "window focus/list"),
        ("uiautomation", "Windows UI element detection (core)"),
        ("requests", "talking to the model backend"),
        ("yaml", "config parsing (PyYAML)"),
        ("psutil", "machine info"),
    ]
    for mod, why in deps:
        present = _has(mod)
        (log.ok if present else log.warn)(
            f"{'found ' if present else 'MISSING'} {mod:<14} - {why}")
        ok = ok and (present or mod in {"yaml", "psutil"})

    for mod, why in [("easyocr", "OCR fallback (optional, heavy)"),
                     ("pyperclip", "clipboard (optional)"),
                     ("sounddevice", "microphone input for voice mode"),
                     ("kokoro_onnx", "local offline TTS (models/tts/)")]:
        (log.ok if _has(mod) else log.info)(
            f"{'found ' if _has(mod) else 'absent'} {mod:<14} - {why}")

    print()
    log.info(f"backend = {cfg.brain.backend}, model = {cfg.brain.model}")
    if cfg.brain.backend == "ollama":
        _check_ollama(cfg)

    print()
    if ok:
        log.ok("core dependencies present. Run 'python run.py' to start.")
    else:
        log.warn("install core deps:  pip install -r requirements.txt")
    return 0 if ok else 1


def _has(mod: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(mod) is not None


def _check_ollama(cfg) -> None:
    try:
        import requests  # type: ignore

        r = requests.get(f"{cfg.brain.base_url}/api/tags", timeout=3)
        tags = [m["name"] for m in r.json().get("models", [])]
        log.ok(f"Ollama up at {cfg.brain.base_url}; models: {tags or '(none pulled)'}")
        if cfg.brain.model not in tags and not any(
                cfg.brain.model.split(":")[0] in t for t in tags):
            log.warn(f"model '{cfg.brain.model}' not pulled. "
                     f"Run: ollama pull {cfg.brain.model}")
    except Exception:
        log.warn(f"Ollama not reachable at {cfg.brain.base_url}. "
                 f"Install from https://ollama.com/download and it auto-starts.")


if __name__ == "__main__":
    sys.exit(main())
