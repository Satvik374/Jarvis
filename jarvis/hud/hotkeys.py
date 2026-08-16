"""System-wide global hotkey manager for Jarvis Floating Mini HUD and shortcuts."""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Optional

from ..utils import logging as log


class GlobalHotkeyManager:
    """Manages system-wide global hotkeys using the keyboard library and native hooks."""

    def __init__(self):
        self._hooks: Dict[str, Any] = {}
        self._callbacks: Dict[str, Callable[[], None]] = {}
        self._is_active = False
        self._lock = threading.Lock()

    @property
    def is_active(self) -> bool:
        return self._is_active

    def register(self, hotkey_str: str, callback: Callable[[], None]) -> bool:
        """Register a global hotkey combination (e.g. 'ctrl+alt+j')."""
        if not hotkey_str:
            return False

        key = hotkey_str.strip().lower()
        with self._lock:
            self._callbacks[key] = callback
            if self._is_active:
                self._bind_hotkey(key, callback)
        return True

    def unregister(self, hotkey_str: str) -> bool:
        key = hotkey_str.strip().lower()
        with self._lock:
            found = False
            if key in self._callbacks:
                del self._callbacks[key]
                found = True
            if key in self._hooks:
                try:
                    import keyboard
                    keyboard.remove_hotkey(self._hooks[key])
                except Exception:
                    pass
                del self._hooks[key]
                found = True
            return found

    def _bind_hotkey(self, key: str, callback: Callable[[], None]) -> None:
        try:
            import keyboard
            if key in self._hooks:
                try:
                    keyboard.remove_hotkey(self._hooks[key])
                except Exception:
                    pass
                del self._hooks[key]

            # Wrap callback in thread to avoid blocking keyboard hook thread
            def _runner():
                try:
                    callback()
                except Exception as exc:
                    log.warn(f"Hotkey '{key}' handler error: {exc}")

            hook_ref = keyboard.add_hotkey(key, lambda: threading.Thread(target=_runner, daemon=True).start())
            self._hooks[key] = hook_ref
            log.info(f"⌨️ Registered global hotkey: {key}")
        except Exception as exc:
            log.warn(f"Could not bind global hotkey '{key}': {exc}")

    def start(self) -> None:
        """Activate all registered hotkeys."""
        with self._lock:
            if self._is_active:
                return
            self._is_active = True
            for key, cb in self._callbacks.items():
                self._bind_hotkey(key, cb)

    def stop(self) -> None:
        """Unhook and deactivate all hotkeys."""
        with self._lock:
            self._is_active = False
            try:
                import keyboard
                for key, hook_ref in self._hooks.items():
                    try:
                        keyboard.remove_hotkey(hook_ref)
                    except Exception:
                        pass
                self._hooks.clear()
            except Exception:
                pass


_HOTKEY_MGR: Optional[GlobalHotkeyManager] = None


def get_hotkey_manager() -> GlobalHotkeyManager:
    global _HOTKEY_MGR
    if _HOTKEY_MGR is None:
        _HOTKEY_MGR = GlobalHotkeyManager()
    return _HOTKEY_MGR
