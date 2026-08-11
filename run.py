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
    try:
        import signal
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, signal.default_int_handler)
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
    # Remote-device modes are deliberately separate from normal task execution.
    # A hosted relay never runs commands; the paired device must run
    # --remote-agent itself before it can receive an encrypted task.
    parser.add_argument("--remote-url", help="override remote relay URL (must be https except localhost)")
    parser.add_argument("--remote-pair", metavar="DEVICE",
                        help="start pairing this controller with a named remote device")
    parser.add_argument("--remote-accept", metavar="CODE",
                        help="accept the one-time pairing code on the other device")
    parser.add_argument("--remote-name", default="",
                        help="this device's display name while pairing")
    parser.add_argument("--remote-trust", nargs=2, metavar=("DEVICE", "FINGERPRINT"),
                        help="trust a pairing after comparing fingerprints on both devices")
    parser.add_argument("--remote-status", action="store_true", help="list local remote pairings")
    parser.add_argument("--list-devices", action="store_true",
                        help="list paired devices with their pairing id and name")
    parser.add_argument("--remove", nargs=2, metavar=("pair", "DEVICE"),
                        help='remove pair <device name>; quote names containing spaces')
    parser.add_argument("--remove-pair", dest="remote_remove_pair", metavar="DEVICE",
                        help="remove a local pairing by device name or pairing id")
    parser.add_argument("--remote-send", nargs=2, metavar=("DEVICE", "TASK"),
                        help="send one task to a trusted paired device and wait for its result")
    parser.add_argument("--remote-agent", action="store_true",
                        help="run this computer as a paired remote Jarvis agent")
    parser.add_argument("--remote-allow-unattended", action="store_true",
                        help="with --remote-agent, force unattended execution for this launch")
    args = parser.parse_args(argv)

    remove_device = args.remote_remove_pair
    if args.remove:
        operation, device = args.remove
        if operation.casefold() != "pair":
            parser.error("--remove supports: --remove pair <device name>")
        if remove_device:
            parser.error("choose either --remove pair or --remove-pair")
        remove_device = device
    args.remote_remove = remove_device

    cfg = load_config()
    if args.backend:
        cfg.brain.backend = args.backend
    if args.model:
        cfg.brain.model = args.model
    if args.adapter:
        cfg.brain.adapter_path = args.adapter
    if args.base_url:
        cfg.brain.base_url = args.base_url
    if args.remote_url:
        cfg.remote.relay_url = args.remote_url
    if args.vision:
        cfg.brain.use_vision = True
    if args.voice:
        cfg.voice_enabled = True
    if args.confirm:
        cfg.safety.confirm_each_action = True
    if args.steps:
        cfg.safety.max_steps = args.steps

    remote_modes = [bool(args.remote_pair), bool(args.remote_accept),
                    bool(args.remote_trust), bool(args.remote_status),
                    bool(args.list_devices), bool(args.remote_remove),
                    bool(args.remote_send), bool(args.remote_agent)]
    if args.remote_allow_unattended and not args.remote_agent:
        parser.error("--remote-allow-unattended only applies with --remote-agent")
    if sum(remote_modes) > 1:
        parser.error("choose exactly one remote mode per command")
    if args.browser and args.remote_agent:
        # Remote-agent browser mode keeps the encrypted worker on this
        # computer and exposes only its loopback dashboard.  The child still
        # runs the exact same remote-agent lifecycle as the CLI.
        child_args: list[str] = ["--remote-agent"]
        for flag, value in (
            ("--backend", args.backend),
            ("--model", args.model),
            ("--adapter", args.adapter),
            ("--base-url", args.base_url),
            ("--remote-url", args.remote_url),
        ):
            if value:
                child_args.extend((flag, value))
        if args.vision:
            child_args.append("--vision")
        if args.confirm:
            child_args.append("--confirm")
        if args.steps:
            child_args.extend(("--steps", str(args.steps)))
        if args.remote_allow_unattended:
            child_args.append("--remote-allow-unattended")

        from jarvis.browser import run_browser

        return run_browser(
            child_args=child_args,
            initial_task=None,
            remote_agent=True,
        )

    if any(remote_modes):
        return _run_remote_mode(args, cfg)

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


def _run_remote_mode(args, cfg) -> int:
    """Run the explicit CLI lifecycle for encrypted remote pairing/control."""
    from jarvis import remote

    try:
        if args.remote_pair:
            offer = remote.start_pairing(cfg, args.remote_pair, local_name=args.remote_name)
            print(f"Pairing code for {offer.label}: {offer.code}")
            print("On the other device, run:")
            print(f'  python run.py --remote-accept {offer.code} --remote-name "{offer.label}"')
            print("Waiting up to 10 minutes for that device to accept the code...")
            pairing = remote.finish_pairing(cfg, offer)
            print(f"Paired with {pairing.peer_name}, but it is NOT trusted yet.")
            print(f"Compare this fingerprint on BOTH devices: {pairing.verification_fingerprint}")
            print(f'Then on this computer run: python run.py --remote-trust "{pairing.label}" {pairing.verification_fingerprint}')
            return 0
        if args.remote_accept:
            pairing = remote.accept_pairing(cfg, args.remote_accept, local_name=args.remote_name)
            print(f"Paired with {pairing.peer_name}, but it is NOT trusted yet.")
            print(f"Compare this fingerprint on BOTH devices: {pairing.verification_fingerprint}")
            print(f'Then on this computer run: python run.py --remote-trust "{pairing.label}" {pairing.verification_fingerprint}')
            return 0
        if args.remote_trust:
            label, supplied = args.remote_trust
            store = remote.PairingStore(cfg.remote.state_dir)
            try:
                pairing = store.get(label, role="controller")
            except remote.RemoteError:
                pairing = store.get(label, role="agent")
            remote.trust_pairing(cfg, pairing.label, supplied, role=pairing.role)
            print(f"Trusted {pairing.label} ({pairing.role}).")
            return 0
        if args.remote_status or args.list_devices:
            print(remote.devices_text(cfg))
            return 0
        if args.remote_remove:
            print(remote.remove_pairing(cfg, args.remote_remove))
            return 0
        if args.remote_send:
            ok, message, _image_path = remote.send_task(
                cfg, args.remote_send[0], args.remote_send[1])
            print(message)
            return 0 if ok else 1
        if args.remote_agent:
            return remote.run_remote_agent(cfg, allow_unattended=args.remote_allow_unattended)
    except remote.RemoteError as exc:
        log.error(str(exc))
        return 1
    return 1


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
