"""Jarvis Shadow Desktop & Virtual Workspace Execution Subsystem."""

from .manager import (
    ShadowDesktopManager,
    ShadowWindow,
    get_shadow_manager,
    is_shadow_enabled,
    toggle_shadow,
    set_shadow_enabled,
)
from .virtual_input import (
    VirtualInputDispatcher,
    get_virtual_input,
)
from .capture import (
    ShadowCaptureEngine,
    get_shadow_capture,
)

__all__ = [
    "ShadowDesktopManager",
    "ShadowWindow",
    "get_shadow_manager",
    "is_shadow_enabled",
    "toggle_shadow",
    "set_shadow_enabled",
    "VirtualInputDispatcher",
    "get_virtual_input",
    "ShadowCaptureEngine",
    "get_shadow_capture",
]
