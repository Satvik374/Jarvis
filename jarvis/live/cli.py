"""Cyber HUD terminal interface for Jarvis Dual-Agent Live Voice mode."""

from __future__ import annotations

import sys
import time

from ..config import Config
from ..utils import logging as log
from .supervisor import LiveVoiceSupervisor


class LiveCliHUD:
    """Interactive console Cyber HUD for the Dual-Agent Live Voice Supervisor."""

    def __init__(self, supervisor: LiveVoiceSupervisor):
        self.supervisor = supervisor
        self.cfg = supervisor.cfg

    def run(self) -> int:
        """Run the interactive live dual-agent session."""
        print()
        log.rule("JARVIS DUAL-AGENT LIVE VOICE SYSTEM", "cyan")
        backend_desc = "Gemini API Key" if self.cfg.live_voice.backend == "api_key" else "Google Cloud ADC"
        print(f"  • Communicating Agent: {self.cfg.live_voice.model} ({self.cfg.live_voice.voice_name}) [Active]")
        print(f"  • Main Worker Agent:   {self.cfg.brain.model} [Background Execution]")
        print(f"  • Speculative Fillers: ENABLED (<20ms perceived response)")
        print(f"  • Auth Backend:        {backend_desc}")
        print(f"  • Mid-Task Narration:  {'ENABLED' if self.cfg.live_voice.narrate_steps else 'DISABLED'}")
        print()
        print("  Interaction Rules:")
        print("    1. Speak naturally into your mic — the Communicating Agent handles all voice chat.")
        print("    2. When you assign a task, the Communicating Agent acknowledges instantly and delegates to the Main Agent.")
        print("    3. You can converse or ask questions at ANY TIME while the Main Agent is working.")
        print("    4. Interrupt anytime: speak over Jarvis (barge-in) or type to cancel (:stop).")
        log.rule("", "cyan")
        print()

        ok = self.supervisor.start()
        if not ok:
            log.error("Failed to initialize Dual-Agent Live Voice Supervisor.")
            return 1

        try:
            while True:
                try:
                    user_input = input("\033[96m[Jarvis Voice Active]\033[0m > ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    log.info("Exiting Dual-Agent Live Mode...")
                    break

                if not user_input:
                    continue

                cmd_lower = user_input.lower()
                if cmd_lower in {"exit", "quit", "q", ":q", ":quit"}:
                    break

                if cmd_lower in {":stop", ":cancel", "stop", "cancel"}:
                    self.supervisor.cancel_active_task()
                    continue

                if cmd_lower in {":status", "status"}:
                    summary = self.supervisor.tracker.get_status_summary()
                    log.info(
                        f"Communicating Agent: {'Connected' if self.supervisor.client.is_connected else 'Reconnecting'} | "
                        f"Main Worker: {summary['status'].upper()} (Step {summary['current_step']}/{summary['total_steps']}) | "
                        f"Action: {summary['current_action'] or 'None'}"
                    )
                    continue

                # If user types something while a question is pending, answer it
                if self.supervisor._waiting_for_answer:
                    log.info(f"Answering mid-task question: '{user_input}'")
                    self.supervisor.answer_question(user_input)
                    continue

                # Route all user speech and text input directly to the Communicating Agent
                log.info(f"User: '{user_input}'")
                self.supervisor.send_user_message(user_input)

        finally:
            self.supervisor.stop()
            log.ok("Dual-Agent session closed.")

        return 0
