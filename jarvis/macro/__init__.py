"""Watch & Learn Macro Subsystem for Jarvis.

Enables recording user desktop actions (clicks, typing, hotkeys, window switches),
optimizing and synthesizing them into reusable Macro Plans, and executing them with
parameter substitution and Long-Term Memory (RAG + Knowledge Graph) synchronization.
"""

from __future__ import annotations

from .manager import Macro, MacroManager, MacroStep, get_macro_manager
from .player import MacroPlayer
from .recorder import MacroRecorder

__all__ = [
    "Macro",
    "MacroStep",
    "MacroRecorder",
    "MacroPlayer",
    "MacroManager",
    "get_macro_manager",
]
