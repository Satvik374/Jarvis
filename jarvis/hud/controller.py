"""Controller linking Agent execution, Voice, Vision, Macros, and the Floating HUD."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

from ..config import Config
from ..utils import logging as log
from .hotkeys import GlobalHotkeyManager, get_hotkey_manager
from .mini_overlay import FloatingMiniHUD


class HudController:
    """Orchestrates floating HUD overlay window and system-wide hotkeys."""

    def __init__(
        self,
        cfg: Optional[Config] = None,
        task_runner: Optional[Callable[[str], Any]] = None,
    ):
        self.cfg = cfg or Config()
        self.task_runner = task_runner
        self.hud_cfg = getattr(self.cfg, "hud", None)
        self.hotkeys = get_hotkey_manager()
        self.overlay: Optional[FloatingMiniHUD] = None
        self.state = "idle"
        self.detail_text = "Neural Link Active · Ready"
        self._is_active = False
        self._is_voice_listening = False
        self._cancel_voice = threading.Event()
        self._task_generation = 0

    def start(self, start_overlay: bool = True) -> None:
        """Start the Floating Mini HUD and register system-wide hotkeys."""
        if self._is_active:
            return
        self._is_active = True

        # 1. Initialize Floating Overlay
        pos = getattr(self.hud_cfg, "position", "bottom_right")
        alpha = getattr(self.hud_cfg, "opacity", 0.94)

        self.overlay = FloatingMiniHUD(
            on_submit_command=self._on_user_submit,
            on_voice_toggle=self.toggle_voice,
            on_vision_trigger=self.trigger_vision,
            on_macro_toggle=self.toggle_macro,
            on_stop_action=self.interrupt,
            position=pos,
            opacity=alpha,
        )
        if start_overlay:
            self.overlay.start()

        # 2. Register Global System-Wide Hotkeys
        hk_toggle = getattr(self.hud_cfg, "hotkey_toggle", "ctrl+alt+j")
        hk_voice = getattr(self.hud_cfg, "hotkey_voice", "alt+v")
        hk_vision = getattr(self.hud_cfg, "hotkey_vision", "ctrl+alt+s")
        hk_macro = getattr(self.hud_cfg, "hotkey_macro", "ctrl+alt+r")
        hk_stop = getattr(self.hud_cfg, "hotkey_stop", "ctrl+alt+x")

        if hk_toggle:
            self.hotkeys.register(hk_toggle, self.toggle_hud)
        if hk_voice:
            self.hotkeys.register(hk_voice, self.toggle_voice)
        if hk_vision:
            self.hotkeys.register(hk_vision, self.trigger_vision)
        if hk_macro:
            self.hotkeys.register(hk_macro, self.toggle_macro)
        if hk_stop:
            self.hotkeys.register(hk_stop, self.interrupt)

        self.hotkeys.start()
        log.info("🚀 Floating Mini HUD & Global Hotkeys initialized.")

    def stop(self) -> None:
        """Tear down overlay and hotkeys."""
        self._is_active = False
        self._cancel_voice.set()
        self._is_voice_listening = False
        self._task_generation += 1
        if self.overlay:
            self.overlay.stop()
            self.overlay = None
        self.hotkeys.stop()

    def interrupt(self) -> None:
        """Instantly silence active speech playback, cancel voice listening, and abort active tasks."""
        try:
            from ..utils import voice
            voice.interrupt_speech()
        except Exception:
            pass

        if self._is_voice_listening:
            self._cancel_voice.set()
            self._is_voice_listening = False
            if self.overlay:
                self.overlay.set_voice_active(False)

        self._task_generation += 1

        self.set_state("idle", detail="Interrupted / Silenced")
        if self.overlay:
            self.overlay.set_response(
                "User Directive",
                "[Response / Speech silenced by user]"
            )

        try:
            from ..browser_worker import emit
            emit("activity", kind="warn", message="Interrupted by user via HUD")
            emit("state", state="listening", label="Awaiting directive")
            emit("input_request", prompt="› ", mode="command")
        except Exception:
            pass

        log.info("⏹ HUD interrupt: active speech silenced and directive cancelled.")

    # -- User Interactions ------------------------------------------------- #
    def _on_user_submit(self, command_text: str) -> None:
        if not self.task_runner:
            log.warn("No task runner wired to HUD controller.")
            self.set_state("idle", detail="Ready")
            if self.overlay:
                self.overlay.set_response(command_text, "Error: Task runner not connected.")
            return

        try:
            from ..utils import voice
            voice.interrupt_speech()
        except Exception:
            pass

        self._task_generation += 1
        gen = self._task_generation

        if self.overlay:
            self.overlay.set_response(command_text, "Thinking...")

        def _exec():
            try:
                self.set_state("thinking", detail=f"Processing: {command_text[:24]}...")
                res = self.task_runner(command_text)
                if self._task_generation != gen:
                    return
                self.set_state("idle", detail="Ready")
                if self.overlay:
                    self.overlay.set_response(command_text, str(res))
            except Exception as exc:
                if self._task_generation != gen:
                    return
                self.set_state("error", detail=f"Error: {str(exc)[:26]}")
                if self.overlay:
                    self.overlay.set_response(command_text, f"Error: {exc}")

        threading.Thread(target=_exec, daemon=True, name="jarvis-hud-exec").start()

    def toggle_hud(self) -> None:
        if self.overlay:
            self.overlay.toggle_visibility()

    def show_hud(self) -> None:
        if self.overlay:
            self.overlay.show()

    def hide_hud(self) -> None:
        if self.overlay:
            self.overlay.hide()

    def is_hud_visible(self) -> bool:
        if self.overlay:
            return self.overlay.is_visible()
        return False

    def hide_hud_sync(self, timeout: float = 0.15) -> None:
        if self.overlay:
            self.overlay.hide_sync(timeout=timeout)

    def show_hud_sync(self, timeout: float = 0.15) -> None:
        if self.overlay:
            self.overlay.show_sync(timeout=timeout)

    def toggle_voice(self) -> None:
        """Toggle Push-to-Talk voice recording: 1st press starts recording, 2nd press stops & transcribes to input."""
        if self._is_voice_listening:
            # 2nd press: Stop recording and let worker transcribe
            log.info("🎤 Push-to-Talk 2nd press: stopping recording and transcribing...")
            self._cancel_voice.set()
            return

        # 1st press: Start recording
        self._is_voice_listening = True
        self._cancel_voice.clear()
        if self.overlay:
            self.overlay.set_voice_active(True)
        self.set_state("listening", detail="🎤 Recording voice (Press Alt+V again to stop)...")

        def _ptt_worker():
            try:
                from ..utils import voice
                from ..agent.brain import make_brain

                # Stop any active TTS speaking immediately so mic is clean
                voice.interrupt_speech()

                # Directly record microphone continuously until Alt+V is pressed again
                wav_bytes = voice.record_until_cancelled(self._cancel_voice, max_seconds=120.0)

                # Reset recording flag
                self._is_voice_listening = False
                if self.overlay:
                    self.overlay.set_voice_active(False)

                if not wav_bytes:
                    self.set_state("idle", detail="Ready")
                    return

                self.set_state("thinking", detail="Transcribing speech...")
                brain = make_brain(self.cfg.brain)
                transcript = voice.transcribe(wav_bytes, brain=brain)

                if not transcript or not transcript.strip():
                    self.set_state("idle", detail="No clear speech understood")
                    return

                text = transcript.strip()
                log.info(f"🎤 Voice PTT transcribed: {text}")

                # Put the transcribed text directly into the HUD's Text Input!
                if self.overlay:
                    self.overlay.set_input_text(text)
                self.set_state("idle", detail="Directive transcribed. Press SEND or Enter.")
            except Exception as exc:
                log.warn(f"Voice PTT error: {exc}")
                self._is_voice_listening = False
                if self.overlay:
                    self.overlay.set_voice_active(False)
                self.set_state("error", detail=f"Voice error: {exc}")

        threading.Thread(target=_ptt_worker, daemon=True, name="jarvis-hud-ptt").start()

    def trigger_vision(self) -> None:
        """Capture live screen and report visual context."""
        self.set_state("perceiving", detail="Capturing visual environment...")

        def _do_see():
            try:
                from ..perception import get_live_vision
                from ..agent.brain import make_brain
                vision = get_live_vision()
                brain = make_brain(self.cfg.brain)
                analysis = vision.analyze(source="screen", prompt="Describe the active screen briefly.", brain=brain)
                self.set_state("success", detail="Vision Analysis Complete")

                try:
                    from ..utils import voice
                    voice.speak(analysis, wait=False)
                except Exception:
                    pass
            except Exception as exc:
                self.set_state("error", detail=f"Vision failed: {exc}")

        threading.Thread(target=_do_see, daemon=True, name="jarvis-hud-vision").start()

    def toggle_macro(self) -> None:
        """Toggle Watch & Learn macro recording."""
        try:
            from ..macro import get_macro_manager
            from ..macro.recorder import get_macro_recorder
            rec = get_macro_recorder(get_macro_manager())

            if not rec.is_recording:
                macro_name = f"quick_macro_{int(time.time())}"
                rec.start_recording(name=macro_name)
                if self.overlay:
                    self.overlay.set_macro_recording(True)
                self.set_state("acting", detail=f"Recording: {macro_name}")
            else:
                macro = rec.stop_recording(save_to_memory=True)
                if self.overlay:
                    self.overlay.set_macro_recording(False)
                macro_title = getattr(macro, "name", "Macro") if macro else "Macro"
                self.set_state("success", detail=f"Learned: {macro_title}")
        except Exception as exc:
            if self.overlay:
                self.overlay.set_macro_recording(False)
            self.set_state("error", detail=f"Macro error: {exc}")

    def set_state(self, state_name: str, detail: Optional[str] = None) -> None:
        self.state = state_name
        if detail is not None:
            self.detail_text = detail
        if self.overlay:
            self.overlay.set_state(state_name, detail=detail)
