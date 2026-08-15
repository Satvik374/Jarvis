"""Playwright & Chrome DevTools Protocol (CDP) Driver for Jarvis.

Provides direct, high-speed programmatic browser control, DOM and accessibility
tree extraction, and instant visual verification for web tasks.
Runs Playwright in a dedicated worker thread for complete safety against asyncio/thread conflicts.
"""

from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..config import Config, load_config
from ..utils import logging as log


@dataclass
class DOMElement:
    """An interactive DOM element identified on the page."""
    id: str                 # e.g. "e1", "e2"
    tag: str                # e.g. "button", "input", "a"
    role: str               # e.g. "button", "textbox", "link"
    text: str               # Visible label or placeholder
    selector: str           # Unique or best-effort CSS selector
    attributes: Dict[str, str] = field(default_factory=dict)
    is_visible: bool = True

    def summary(self) -> str:
        parts = [f"[{self.id}]", f"<{self.tag}>"]
        if self.role and self.role != self.tag:
            parts.append(f"role={self.role}")
        if self.text:
            cleaned_text = " ".join(self.text.split())
            if len(cleaned_text) > 40:
                cleaned_text = cleaned_text[:37] + "..."
            parts.append(f'"{cleaned_text}"')
        if self.attributes.get("type"):
            parts.append(f"type={self.attributes['type']}")
        if self.attributes.get("name"):
            parts.append(f"name={self.attributes['name']}")
        if self.attributes.get("placeholder"):
            parts.append(f"placeholder=\"{self.attributes['placeholder']}\"")
        return " ".join(parts)


@dataclass
class BrowserSnapshot:
    """State snapshot of the current web page."""
    url: str
    title: str
    elements: List[DOMElement] = field(default_factory=list)
    headings: List[str] = field(default_factory=list)
    text_content: str = ""
    screenshot_path: Optional[str] = None

    def format_text(self, max_elements: int = 40) -> str:
        lines = [
            f"Page Title: {self.title}",
            f"URL: {self.url}",
        ]
        if self.headings:
            lines.append("Headings: " + " | ".join(self.headings[:6]))

        if self.elements:
            lines.append(f"\nInteractive Elements ({len(self.elements)} total, showing top {min(len(self.elements), max_elements)}):")
            for el in self.elements[:max_elements]:
                lines.append(f"  • {el.summary()}")
        else:
            lines.append("\n(No prominent interactive elements found)")

        return "\n".join(lines)


# JavaScript injected to extract visible, interactive elements with generated CSS selectors
_DOM_EXTRACT_SCRIPT = """
() => {
    function getCssSelector(el) {
        if (el.id) return `#${CSS.escape(el.id)}`;
        if (el.name) return `${el.tagName.toLowerCase()}[name="${CSS.escape(el.name)}"]`;
        if (el.getAttribute('data-testid')) return `[data-testid="${CSS.escape(el.getAttribute('data-testid'))}"]`;
        if (el.getAttribute('aria-label')) return `[aria-label="${CSS.escape(el.getAttribute('aria-label'))}"]`;
        
        let path = [];
        let curr = el;
        while (curr && curr.nodeType === Node.ELEMENT_NODE && curr !== document.body) {
            let selector = curr.tagName.toLowerCase();
            if (curr.className && typeof curr.className === 'string') {
                let classes = curr.className.trim().split(/\\s+/).filter(c => c && !c.includes(':') && !c.startsWith('_'));
                if (classes.length > 0) {
                    selector += '.' + classes.slice(0, 2).map(c => CSS.escape(c)).join('.');
                }
            }
            let parent = curr.parentNode;
            if (parent && parent.children) {
                let siblings = Array.from(parent.children).filter(c => c.tagName === curr.tagName);
                if (siblings.length > 1) {
                    let index = siblings.indexOf(curr) + 1;
                    selector += `:nth-of-type(${index})`;
                }
            }
            path.unshift(selector);
            if (curr.id) break;
            curr = parent;
        }
        return path.join(' > ');
    }

    function isVisible(el) {
        if (!el.getBoundingClientRect) return false;
        const rect = el.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return false;
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
        return true;
    }

    const interactiveSelectors = [
        'button', 'a[href]', 'input', 'textarea', 'select',
        '[role="button"]', '[role="link"]', '[role="checkbox"]', '[role="radio"]',
        '[role="textbox"]', '[role="combobox"]', '[role="tab"]', '[role="menuitem"]',
        '[onclick]', '[tabindex]:not([tabindex="-1"])'
    ];

    const elements = [];
    const seen = new Set();
    const candidates = document.querySelectorAll(interactiveSelectors.join(', '));

    let idx = 1;
    for (const el of candidates) {
        if (!isVisible(el) || seen.has(el)) continue;
        seen.add(el);

        let text = (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim();
        let role = el.getAttribute('role') || el.tagName.toLowerCase();
        let attrs = {};
        if (el.type) attrs['type'] = el.type;
        if (el.name) attrs['name'] = el.name;
        if (el.placeholder) attrs['placeholder'] = el.placeholder;
        if (el.href) attrs['href'] = el.href;

        elements.push({
            id: `e${idx++}`,
            tag: el.tagName.toLowerCase(),
            role: role,
            text: text,
            selector: getCssSelector(el),
            attributes: attrs
        });
    }

    const headings = Array.from(document.querySelectorAll('h1, h2, h3'))
        .filter(isVisible)
        .map(h => (h.innerText || '').trim())
        .filter(t => t.length > 0);

    return {
        elements: elements,
        headings: headings,
        title: document.title,
        url: window.location.href
    };
}
"""


class _BrowserWorker(threading.Thread):
    """Dedicated single-threaded Playwright executor."""

    def __init__(self, cfg: Config):
        super().__init__(daemon=True, name="JarvisBrowserWorker")
        self.cfg = cfg
        self._work_queue: queue.Queue = queue.Queue()
        self._ready_event = threading.Event()
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._element_map: Dict[str, str] = {}
        self._shot_dir = Path(__file__).resolve().parent.parent.parent / "dataset" / "data" / "screenshots"
        self._shot_dir.mkdir(parents=True, exist_ok=True)
        self.start()
        self._ready_event.wait()

    def run(self) -> None:
        self._ready_event.set()
        while True:
            item = self._work_queue.get()
            if item is None:
                self._cleanup()
                self._work_queue.task_done()
                break

            func, args, kwargs, res_queue = item
            try:
                res = func(*args, **kwargs)
                res_queue.put((True, res))
            except Exception as exc:
                res_queue.put((False, exc))
            finally:
                self._work_queue.task_done()

    def execute(self, func: Callable, *args: Any, **kwargs: Any) -> Any:
        res_queue: queue.Queue = queue.Queue()
        self._work_queue.put((func, args, kwargs, res_queue))
        ok, val = res_queue.get()
        if not ok:
            raise val
        return val

    def stop(self) -> None:
        self._work_queue.put(None)
        self.join(timeout=3.0)

    # ------------------------------------------------------------------ #
    # Internal Browser Execution
    # ------------------------------------------------------------------ #

    def _ensure_browser(self, headless_override: Optional[bool] = None) -> None:
        if self._page and not self._page.is_closed():
            return

        from playwright.sync_api import sync_playwright

        if self._playwright is None:
            self._playwright = sync_playwright().start()

        b_cfg = self.cfg.browser
        headless = b_cfg.headless if headless_override is None else headless_override

        # 1. Connect over CDP if configured
        if b_cfg.cdp_url:
            log.info(f"Connecting to Chrome over CDP: {b_cfg.cdp_url}")
            try:
                self._browser = self._playwright.chromium.connect_over_cdp(b_cfg.cdp_url)
                if self._browser.contexts:
                    self._context = self._browser.contexts[0]
                else:
                    self._context = self._browser.new_context(
                        viewport={"width": b_cfg.viewport_width, "height": b_cfg.viewport_height}
                    )
                self._page = self._context.new_page() if not self._context.pages else self._context.pages[0]
                return
            except Exception as exc:
                log.warn(f"CDP connection failed ({exc}), falling back to local browser launch.")

        # 2. Persistent Context / Local Launch
        user_data_dir = b_cfg.user_data_dir or str(Path.home() / ".jarvis" / "browser_profile")
        Path(user_data_dir).mkdir(parents=True, exist_ok=True)

        browser_type_name = (b_cfg.browser_type or "chromium").lower()
        if browser_type_name in {"chrome", "msedge"}:
            channel = browser_type_name
            engine = self._playwright.chromium
        elif browser_type_name == "firefox":
            channel = None
            engine = self._playwright.firefox
        elif browser_type_name == "webkit":
            channel = None
            engine = self._playwright.webkit
        else:
            channel = None
            engine = self._playwright.chromium

        try:
            launch_args = {
                "user_data_dir": user_data_dir,
                "headless": headless,
                "viewport": {"width": b_cfg.viewport_width, "height": b_cfg.viewport_height},
            }
            if channel:
                launch_args["channel"] = channel

            self._context = engine.launch_persistent_context(**launch_args)
            self._page = self._context.new_page() if not self._context.pages else self._context.pages[0]
            self._page.set_default_timeout(b_cfg.timeout)
        except Exception as exc:
            log.warn(f"Persistent context launch failed: {exc}, launching non-persistent browser.")
            launch_kwargs = {"headless": headless}
            if channel:
                launch_kwargs["channel"] = channel
            self._browser = engine.launch(**launch_kwargs)
            self._context = self._browser.new_context(
                viewport={"width": b_cfg.viewport_width, "height": b_cfg.viewport_height}
            )
            self._page = self._context.new_page()
            self._page.set_default_timeout(b_cfg.timeout)

    def _resolve_target(self, target: str) -> str:
        target = (target or "").strip()
        if not target:
            return ""
        m = re.match(r"^\[?e(\d+)\]?$", target, re.IGNORECASE)
        if m:
            key = f"e{m.group(1)}"
            if key in self._element_map:
                return self._element_map[key]
        return target

    def _extract_snapshot(self) -> BrowserSnapshot:
        try:
            raw_data = self._page.evaluate(_DOM_EXTRACT_SCRIPT)
        except Exception:
            raw_data = {"elements": [], "headings": [], "title": self._page.title() or "", "url": self._page.url or ""}

        elements: List[DOMElement] = []
        self._element_map.clear()

        for item in raw_data.get("elements", []):
            el = DOMElement(
                id=item["id"],
                tag=item["tag"],
                role=item.get("role", ""),
                text=item.get("text", ""),
                selector=item["selector"],
                attributes=item.get("attributes", {}),
            )
            elements.append(el)
            self._element_map[el.id] = el.selector

        return BrowserSnapshot(
            url=raw_data.get("url", self._page.url),
            title=raw_data.get("title", self._page.title()),
            elements=elements,
            headings=raw_data.get("headings", []),
        )

    def _capture_screenshot(self, path: Optional[str] = None, full_page: bool = False) -> str:
        if not path:
            ts = time.strftime("%Y%m%d_%H%M%S")
            p = self._shot_dir / f"browser_{ts}_{int(time.time() * 1000) % 1000:03d}.png"
        else:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)

        try:
            self._page.screenshot(path=str(p), full_page=full_page)
            return str(p)
        except Exception as exc:
            log.warn(f"Browser screenshot failed: {exc}")
            return ""

    def _cleanup(self) -> None:
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception as exc:
            log.warn(f"Error closing browser: {exc}")
        finally:
            self._page = None
            self._context = None
            self._browser = None
            self._playwright = None
            self._element_map.clear()


class BrowserDriver:
    """Thread-safe and Async-safe Playwright & CDP Browser Controller."""

    def __init__(self, cfg: Optional[Config] = None):
        self.cfg = cfg or load_config()
        self._worker = _BrowserWorker(self.cfg)

    # ------------------------------------------------------------------ #
    # High-Level API
    # ------------------------------------------------------------------ #

    def navigate(self, url: str, wait_until: str = "load", headless: Optional[bool] = None) -> Dict[str, Any]:
        def _impl():
            self._worker._ensure_browser(headless_override=headless)
            target_url = url
            if not (target_url.startswith("http://") or target_url.startswith("https://") or target_url.startswith("file://")):
                target_url = "https://" + target_url

            try:
                self._worker._page.goto(target_url, wait_until=wait_until, timeout=self.cfg.browser.timeout)
            except Exception:
                try:
                    self._worker._page.goto(target_url, wait_until="domcontentloaded", timeout=15000)
                except Exception:
                    pass

            snap = self._worker._extract_snapshot()
            shot_path = self._worker._capture_screenshot()
            return {
                "ok": True,
                "url": self._worker._page.url,
                "title": self._worker._page.title(),
                "snapshot": snap.format_text(),
                "screenshot_path": shot_path,
            }
        return self._worker.execute(_impl)

    def click(self, target: str, timeout: int = 5000) -> Dict[str, Any]:
        def _impl():
            self._worker._ensure_browser()
            selector = self._worker._resolve_target(target)
            if not selector:
                return {"ok": False, "message": f"Target '{target}' could not be resolved."}

            try:
                loc = self._worker._page.locator(selector).first
                loc.click(timeout=timeout)
            except Exception as exc:
                try:
                    text_selector = f"text={target}"
                    self._worker._page.locator(text_selector).first.click(timeout=3000)
                except Exception:
                    return {"ok": False, "message": f"Failed to click '{target}' (selector: {selector}): {exc}"}

            time.sleep(0.5)
            snap = self._worker._extract_snapshot()
            shot_path = self._worker._capture_screenshot()
            return {
                "ok": True,
                "url": self._worker._page.url,
                "title": self._worker._page.title(),
                "message": f"Clicked '{target}' successfully.",
                "snapshot": snap.format_text(),
                "screenshot_path": shot_path,
            }
        return self._worker.execute(_impl)

    def type_text(
        self,
        target: str,
        text: str,
        clear: bool = True,
        press_enter: bool = False,
        timeout: int = 5000,
    ) -> Dict[str, Any]:
        def _impl():
            self._worker._ensure_browser()
            selector = self._worker._resolve_target(target)
            if not selector:
                return {"ok": False, "message": f"Target '{target}' could not be resolved."}

            try:
                loc = self._worker._page.locator(selector).first
                if clear:
                    loc.fill(text, timeout=timeout)
                else:
                    loc.type(text, timeout=timeout)

                if press_enter:
                    loc.press("Enter")
                    time.sleep(0.5)
            except Exception as exc:
                return {"ok": False, "message": f"Failed to type into '{target}': {exc}"}

            snap = self._worker._extract_snapshot()
            shot_path = self._worker._capture_screenshot()
            return {
                "ok": True,
                "url": self._worker._page.url,
                "title": self._worker._page.title(),
                "message": f"Typed '{text}' into '{target}'" + (" and pressed Enter." if press_enter else "."),
                "snapshot": snap.format_text(),
                "screenshot_path": shot_path,
            }
        return self._worker.execute(_impl)

    def select_option(self, target: str, value: str, timeout: int = 5000) -> Dict[str, Any]:
        def _impl():
            self._worker._ensure_browser()
            selector = self._worker._resolve_target(target)
            try:
                loc = self._worker._page.locator(selector).first
                loc.select_option(value=value, timeout=timeout)
            except Exception as exc:
                return {"ok": False, "message": f"Failed to select option on '{target}': {exc}"}

            snap = self._worker._extract_snapshot()
            shot_path = self._worker._capture_screenshot()
            return {
                "ok": True,
                "message": f"Selected option '{value}' on '{target}'.",
                "snapshot": snap.format_text(),
                "screenshot_path": shot_path,
            }
        return self._worker.execute(_impl)

    def scroll(self, direction: str = "down", amount: int = 500) -> Dict[str, Any]:
        def _impl():
            self._worker._ensure_browser()
            delta_y = amount if direction.lower() == "down" else -amount
            if direction.lower() == "top":
                self._worker._page.evaluate("window.scrollTo(0, 0)")
            elif direction.lower() == "bottom":
                self._worker._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            else:
                self._worker._page.mouse.wheel(0, delta_y)

            time.sleep(0.3)
            snap = self._worker._extract_snapshot()
            shot_path = self._worker._capture_screenshot()
            return {
                "ok": True,
                "message": f"Scrolled {direction} by {amount}px.",
                "snapshot": snap.format_text(),
                "screenshot_path": shot_path,
            }
        return self._worker.execute(_impl)

    def hover(self, target: str, timeout: int = 5000) -> Dict[str, Any]:
        def _impl():
            self._worker._ensure_browser()
            selector = self._worker._resolve_target(target)
            try:
                self._worker._page.locator(selector).first.hover(timeout=timeout)
            except Exception as exc:
                return {"ok": False, "message": f"Failed to hover over '{target}': {exc}"}

            time.sleep(0.3)
            snap = self._worker._extract_snapshot()
            shot_path = self._worker._capture_screenshot()
            return {
                "ok": True,
                "message": f"Hovered over '{target}'.",
                "snapshot": snap.format_text(),
                "screenshot_path": shot_path,
            }
        return self._worker.execute(_impl)

    def press_key(self, key: str) -> Dict[str, Any]:
        def _impl():
            self._worker._ensure_browser()
            self._worker._page.keyboard.press(key)
            time.sleep(0.3)
            snap = self._worker._extract_snapshot()
            shot_path = self._worker._capture_screenshot()
            return {
                "ok": True,
                "message": f"Pressed key '{key}'.",
                "snapshot": snap.format_text(),
                "screenshot_path": shot_path,
            }
        return self._worker.execute(_impl)

    def evaluate(self, script: str) -> Dict[str, Any]:
        def _impl():
            self._worker._ensure_browser()
            try:
                res = self._worker._page.evaluate(script)
                res_str = json.dumps(res, default=str) if isinstance(res, (dict, list)) else str(res)
                return {"ok": True, "result": res_str}
            except Exception as exc:
                return {"ok": False, "message": f"JavaScript evaluation error: {exc}"}
        return self._worker.execute(_impl)

    def extract_content(
        self,
        target: Optional[str] = None,
        mode: str = "markdown",
        max_chars: int = 8000,
    ) -> Dict[str, Any]:
        def _impl():
            self._worker._ensure_browser()
            try:
                if target:
                    selector = self._worker._resolve_target(target)
                    loc = self._worker._page.locator(selector).first
                    content = loc.inner_text() if mode == "text" else loc.inner_html()
                else:
                    if mode == "html":
                        content = self._worker._page.content()
                    else:
                        js_extract = """
                        () => {
                            const clone = document.body.cloneNode(true);
                            const removeTags = ['script', 'style', 'noscript', 'svg', 'iframe'];
                            removeTags.forEach(tag => {
                                clone.querySelectorAll(tag).forEach(el => el.remove());
                            });
                            return clone.innerText || '';
                        }
                        """
                        content = self._worker._page.evaluate(js_extract)

                content_trimmed = (content or "").strip()[:max_chars]
                return {
                    "ok": True,
                    "url": self._worker._page.url,
                    "title": self._worker._page.title(),
                    "content": content_trimmed,
                }
            except Exception as exc:
                return {"ok": False, "message": f"Content extraction failed: {exc}"}
        return self._worker.execute(_impl)

    def snapshot(self) -> BrowserSnapshot:
        def _impl():
            self._worker._ensure_browser()
            snap = self._worker._extract_snapshot()
            snap.screenshot_path = self._worker._capture_screenshot()
            return snap
        return self._worker.execute(_impl)

    def take_screenshot(self, path: Optional[str] = None, full_page: bool = False) -> str:
        def _impl():
            self._worker._ensure_browser()
            return self._worker._capture_screenshot(path=path, full_page=full_page)
        return self._worker.execute(_impl)

    def close(self) -> None:
        try:
            self._worker.execute(self._worker._cleanup)
        except Exception:
            pass


# Global singleton instance
_GLOBAL_DRIVER: Optional[BrowserDriver] = None


def get_browser_driver(cfg: Optional[Config] = None) -> BrowserDriver:
    global _GLOBAL_DRIVER
    if _GLOBAL_DRIVER is None:
        _GLOBAL_DRIVER = BrowserDriver(cfg=cfg)
    return _GLOBAL_DRIVER
