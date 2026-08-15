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
        self._is_active = False

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
            position=pos,
            opacity=alpha,
        )
        if start_overlay:
            self.overlay.start()

        # 2. Register Global System-Wide Hotkeys
        hk_toggle = getattr(self.hud_cfg, "hotkey_toggle", "ctrl+alt+j")
        hk_voice = getattr(self.hud_cfg, "hotkey_voice", "ctrl+alt+v")
        hk_vision = getattr(self.hud_cfg, "hotkey_vision", "ctrl+alt+s")
        hk_macro = getattr(self.hud_cfg, "hotkey_macro", "ctrl+alt+r")

        if hk_toggle:
            self.hotkeys.register(hk_toggle, self.toggle_hud)
        if hk_voice:
            self.hotkeys.register(hk_voice, self.toggle_voice)
        if hk_vision:
            self.hotkeys.register(hk_vision, self.trigger_vision)
        if hk_macro:
            self.hotkeys.register(hk_macro, self.toggle_macro)

        self.hotkeys.start()
        log.info("🚀 Floating Mini HUD & Global Hotkeys initialized.")

    def stop(self) -> None:
        """Tear down overlay and hotkeys."""
        self._is_active = False
        if self.overlay:
            self.overlay.stop()
            self.overlay = None
        self.hotkeys.stop()

    # -- User Interactions ------------------------------------------------- #
    def _on_user_submit(self, command_text: str) -> None:
        if not self.task_runner:
            log.warn("No task runner wired to HUD controller.")
            return

        def _exec():
            try:
                self.set_state("acting", detail=f"Executing: {command_text[:24]}...")
                res = self.task_runner(command_text)
                self.set_state("success", detail=f"{str(res)[:30]}")
            except Exception as exc:
                self.set_state("error", detail=f"Error: {str(exc)[:26]}")

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

    def toggle_voice(self) -> None:
        """Toggle Push-to-Talk or Voice listening."""
        if not self.overlay:
            return
        new_voice_state = not self.overlay.is_voice_active
        self.overlay.set_voice_active(new_voice_state)
        if new_voice_state:
            self.set_state("listening", detail="Voice listening...")
            try:
                from ..utils import voice
                voice.speak("Listening, sir.", wait=False)
            except Exception:
                pass
        else:
            self.set_state("idle", detail="Ready")

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
        if not self.overlay:
            return
        try:
            from ..macro import get_macro_manager
            from ..macro.recorder import get_macro_recorder
            rec = get_macro_recorder(get_macro_manager())

            if not rec.is_recording:
                macro_name = f"quick_macro_{int(time.time())}"
                rec.start_recording(name=macro_name)
                self.overlay.set_macro_recording(True)
                self.set_state("acting", detail=f"Recording: {macro_name}")
            else:
                macro = rec.stop_recording(save_to_memory=True)
                self.overlay.set_macro_recording(False)
                self.set_state("success", detail=f"Learned: {macro.name}")
        except Exception as exc:
            self.set_state("error", detail=f"Macro error: {exc}")

    def set_state(self, state_name: str, detail: Optional[str] = None) -> None:
        if self.overlay:
            self.overlay.set_state(state_name, detail=detail)
