"""Direct Browser Automation Engine for Jarvis (Playwright / CDP).

Enables sub-50ms programmatic browser interaction, DOM accessibility-tree
inspection, JS execution, form automation, and instant visual verification.
"""

from __future__ import annotations

from .driver import BrowserDriver, BrowserSnapshot, DOMElement, get_browser_driver

__all__ = [
    "BrowserDriver",
    "BrowserSnapshot",
    "DOMElement",
    "get_browser_driver",
]
