#!/usr/bin/env python
"""Jarvis launcher.

Usage:
  python run.py                      # interactive console
  python run.py --browser            # interactive cyber UI in your browser
  python run.py "open notepad and type hello"   # run one task then exit
  python run.py --check              # environment / dependency check
  python run.py --backend ollama --model ornith:9b "..."
  python run.py --wake               # hands-free: say "Hey Jarvis" to command
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
        if hasattr(signal, "SIGINT"):
            signal.signal(signal.SIGINT, signal.default_int_handler)
    except Exception:
        pass
    try:
        import _thread
        from ctypes import wintypes

        _HANDLER = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

        def _ctrl_handler(dwCtrlType: int) -> bool:
            # 0=CTRL_C_EVENT, 1=CTRL_BREAK_EVENT, 2=CTRL_CLOSE_EVENT
            if dwCtrlType in (0, 1, 2):
                try:
                    import _thread
                    _thread.interrupt_main()
                except Exception:
                    pass
                return False
            return False

        _GLOBAL_CTRL_HANDLER = _HANDLER(_ctrl_handler)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_GLOBAL_CTRL_HANDLER, True)
    except Exception:
        pass


import argparse
import os


from jarvis.config import load_config
from jarvis.utils import logging as log


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Jarvis - local agentic desktop assistant")
    parser.add_argument("task", nargs="*", help="a task to run once, then exit")
    parser.add_argument("--backend", help="override brain backend (ollama/openai/openrouter/anthropic/llamacpp)")
    parser.add_argument("--model", help="override model name")
    parser.add_argument("--adapter", help="path to a trained LoRA adapter (hf backend)")
    parser.add_argument("--base-url", dest="base_url", help="override backend base URL")
    parser.add_argument("--vision", action="store_true", help="send screenshots to the model")
    parser.add_argument("--shadow", action="store_true", default=None, help="run tasks in isolated shadow desktop (zero mouse interference)")
    parser.add_argument("--no-shadow", action="store_false", dest="shadow", help="disable shadow desktop mode")
    parser.add_argument("--voice", action="store_true", help="speak replies aloud (and voice input in console)")
    parser.add_argument("--live", "--voice-live", dest="live", action="store_true",
                        help="launch straight into Real-Time Gemini Live Voice Supervisor mode")
    parser.add_argument("--live-model", dest="live_model", help="override live voice model name")
    parser.add_argument("--live-voice", dest="live_voice", help="override live voice name (Aoede/Puck/Charon/Kore/Fenrir)")
    parser.add_argument("--terminal-live", dest="terminal_live", action="store_true",
                        help="launch Live Voice Supervisor in the terminal console rather than the browser")

    parser.add_argument("--wake", action="store_true", help='launch straight into hands-free mode: say "Hey Jarvis" to command')
    parser.add_argument("--confirm", action="store_true", help="confirm each action")
    parser.add_argument("--steps", type=int, help="max steps per task")
    parser.add_argument("--check", action="store_true", help="run an environment check and exit")
    parser.add_argument("--codex-login", action="store_true",
                        help="sign in with ChatGPT via OpenAI Codex OAuth flow (PKCE S256)")
    parser.add_argument("--codex-status", action="store_true",
                        help="check OpenAI Codex OAuth session status")
    parser.add_argument("--codex-logout", action="store_true",
                        help="log out and remove saved ChatGPT Codex credentials")
    parser.add_argument("--azure-login", action="store_true",
                        help="sign in to Microsoft Azure AI Foundry via browser")
    parser.add_argument("--azure-status", action="store_true",
                        help="check Microsoft Azure AI Foundry credential status")
    parser.add_argument("--azure-tts", nargs="?", const="Hello, welcome to Azure AI Foundry!",
                        help="test Microsoft Azure Cognitive Services Speech TTS synthesis")
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
    if args.shadow is not None:
        cfg.shadow.enabled = args.shadow
    if cfg.shadow.enabled:
        from jarvis.desktop import set_shadow_enabled
        set_shadow_enabled(True)
    if args.voice:
        cfg.voice_enabled = True
    if args.live:
        cfg.live_voice.enabled = True
    if args.live_model:
        cfg.live_voice.model = args.live_model
    if args.live_voice:
        cfg.live_voice.voice_name = args.live_voice

    if args.wake:
        cfg.wake_enabled = True
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

    if args.codex_login:
        from jarvis.auth import codex_oauth
        codex_oauth.login()
        return 0

    if args.codex_status:
        import time
        from jarvis.auth import codex_oauth
        tokens = codex_oauth.load_tokens()
        if tokens.get("access_token"):
            exp_in = tokens.get("expires_at", 0.0) - time.time()
            log.ok(f"OpenAI Codex session active (Source: {tokens.get('source')})")
            log.info(f"ChatGPT Account ID: {tokens.get('chatgpt_account_id')}")
            log.info(f"Access Token Expires in: {int(exp_in)} seconds (~{int(exp_in/3600)} hours)")
        else:
            log.warn("OpenAI Codex session not found. Run 'python run.py --codex-login' to authenticate.")
        return 0

    if args.codex_logout:
        from jarvis.auth import codex_oauth
        removed = codex_oauth.logout()
        if removed:
            log.ok("Logged out of OpenAI Codex. Saved credentials removed.")
        else:
            log.info("No active OpenAI Codex session found to log out.")
        return 0

    if args.azure_login:
        from jarvis.auth import azure_auth
        azure_auth.login(tenant_id=getattr(cfg.brain, "azure_tenant_id", None))
        return 0

    if args.azure_status:
        from jarvis.auth import azure_auth
        st = azure_auth.check_auth_status(tenant_id=getattr(cfg.brain, "azure_tenant_id", None))
        if st.get("authenticated"):
            log.ok("Microsoft Azure AI Foundry authentication is active and valid.")
        else:
            log.warn(f"Microsoft Azure credentials not ready: {st.get('error')}")
            log.info("Run 'python run.py --azure-login' to sign in via your browser.")
        return 0

    if args.azure_tts is not None:
        from jarvis.utils import voice
        tts_text = args.azure_tts or "Hello, welcome to Azure AI Foundry!"
        log.info(f"Testing Azure Cognitive Services Speech TTS with: '{tts_text}'")
        try:
            wav_data = voice._synthesize_azure_speech(tts_text, cfg.voice)
            log.ok(f"Successfully synthesized {len(wav_data)} bytes of WAV audio via Azure Speech SDK!")
            log.info("Playing synthesized speech...")
            voice._play_wav(wav_data, wait=True)
            log.ok("Audio playback completed.")
            return 0
        except Exception as exc:
            log.error(f"Azure Speech synthesis failed: {exc}")
            return 1

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

    if (args.live or cfg.live_voice.enabled) and not getattr(args, "terminal_live", False):
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
        return run_browser(child_args=child_args, initial_task=initial_task, live_voice=True)

    if args.live or cfg.live_voice.enabled:
        from jarvis.live import run_live_mode
        return run_live_mode(cfg)

    if args.task:
        task_str = " ".join(args.task).strip()
        from jarvis.agent.brain import make_brain, BrainError
        from jarvis.agent.loop import Agent
        from jarvis.live.speculative import get_fast_filler
        from jarvis.live.telemetry_state import TaskTelemetryTracker
        try:
            agent = Agent(make_brain(cfg.brain), cfg)
        except BrainError as exc:
            log.error(str(exc))
            return 1
        from jarvis.utils import voice
        voice.configure(agent.brain, cfg.voice)

        # 1. Speculative Fast Filler (<20ms instant acknowledgment by Communicating Agent)
        fast_filler = get_fast_filler(task_str)
        log.jarvis(f"🎙️ [Communicating Agent]: {fast_filler}")
        voice.speak(fast_filler, wait=False)

        # 2. Main Worker Agent executes in background with real-time telemetry
        tracker = TaskTelemetryTracker()
        tracker.reset_for_new_task(task_str, max_steps=cfg.safety.max_steps)

        import sys as _sys
        asker = None
        if _sys.stdin.isatty():
            from jarvis.console import _typed_asker
            asker = _typed_asker

        try:
            result = agent.run(task_str, asker=asker, on_progress=tracker.update_event)
            log.jarvis(result)
            try:
                voice.speak(result, wait=True)
            except Exception:
                pass
            return 0
        except KeyboardInterrupt:
            print()
            log.warn("Task cancelled by user.")
            try:
                agent.cancel()
            except Exception:
                pass
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
                     ("websockets", "Gemini 3.1 Flash Live Voice streaming"),
                     ("google.auth", "Google Cloud Vertex AI / ADC auth"),
                     ("kokoro_onnx", "local offline TTS (models/tts/)"),
                     ("azure.cognitiveservices.speech", "Microsoft Azure Cognitive Services Speech SDK")]:
        (log.ok if _has(mod) else log.info)(
            f"{'found ' if _has(mod) else 'absent'} {mod:<14} - {why}")

    print()
    log.info(f"backend = {cfg.brain.backend}, model = {cfg.brain.model}")
    if cfg.brain.backend == "gemini":
        if cfg.brain.api_key:
            log.ok(f"Gemini API key configured for model {cfg.brain.model}")
        else:
            log.info(f"Gemini Vertex AI ADC backend for model {cfg.brain.model}")
    elif cfg.brain.backend in {"openrouter", "openai"}:
        if cfg.brain.api_key:
            masked = cfg.brain.api_key[:8] + "..." + cfg.brain.api_key[-4:] if len(cfg.brain.api_key) > 12 else "***"
            log.ok(f"{cfg.brain.backend.title()} API key configured ({masked}) for model {cfg.brain.model}")
        else:
            log.warn(f"No API key configured for {cfg.brain.backend} backend.")
    elif cfg.brain.backend == "ollama":
        _check_ollama(cfg)
    elif cfg.brain.backend in {"codex", "openai-codex", "chatgpt"}:
        from jarvis.auth import codex_oauth
        tokens = codex_oauth.load_tokens()
        if tokens.get("access_token"):
            log.ok(f"OpenAI Codex OAuth session active (Account: {tokens.get('chatgpt_account_id') or 'detected'}) for model {cfg.brain.model}")
        else:
            log.warn("OpenAI Codex session not found. Run 'python run.py --codex-login' to sign in with ChatGPT.")
    elif cfg.brain.backend in {"foundry", "azure", "azure-foundry", "azure_foundry", "foundry-agent"}:
        endpoint = getattr(cfg.brain, "foundry_endpoint", None) or cfg.brain.base_url or "https://satviksingh-resource.services.ai.azure.com/api/projects/satviksingh"
        agent = getattr(cfg.brain, "foundry_agent_name", None) or cfg.brain.model or "gpt-6"
        version = getattr(cfg.brain, "foundry_agent_version", None) or "1"
        log.ok(f"Microsoft Foundry Agent configured: {agent} (v{version}) at {endpoint}")
        from jarvis.auth import azure_auth
        st = azure_auth.check_auth_status(tenant_id=getattr(cfg.brain, "azure_tenant_id", None))
        if st.get("authenticated"):
            log.ok("Azure credentials active (DefaultAzureCredential)")
        elif os.environ.get("AZURE_API_KEY") or os.environ.get("AZURE_AI_KEY") or (cfg.brain.api_key and not (cfg.brain.api_key.startswith("sk-or-") or cfg.brain.api_key.startswith("AIza"))):
            log.ok("Azure API key configured")
        else:
            log.info("Azure credentials will auto-authenticate via interactive browser sign-in on first request, or run 'python run.py --azure-login'.")

    # Check Azure Cognitive Services Speech status
    speech_key = getattr(cfg.voice, "azure_speech_key", "") or os.environ.get("AZURE_SPEECH_KEY") or os.environ.get("SPEECH_KEY")
    speech_voice = getattr(cfg.voice, "azure_speech_voice", "en-US-OnyxTurboMultilingualNeural")
    speech_endpoint = getattr(cfg.voice, "azure_speech_endpoint", "https://satviksingh-resource.cognitiveservices.azure.com/")
    if speech_key:
        masked_k = speech_key[:4] + "..." + speech_key[-4:] if len(speech_key) > 8 else "***"
        log.ok(f"Azure Speech TTS active (engine={cfg.voice.engine}, voice={speech_voice}, key={masked_k}) at {speech_endpoint}")
    elif cfg.voice.engine in {"azure", "azure_speech", "foundry"}:
        log.info(f"Azure Speech TTS selected (voice={speech_voice}). Set AZURE_SPEECH_KEY in .env to activate.")

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
    try:
        code = main()
        import os, sys
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code if isinstance(code, int) else 0)
    except KeyboardInterrupt:
        print()
        import os, sys
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)
    except SystemExit as exc:
        import os, sys
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(exc.code if isinstance(exc.code, int) else 0)
    except Exception as exc:
        log.error(f"Fatal error: {exc}")
        import os, sys
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1)
