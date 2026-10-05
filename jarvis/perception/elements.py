"""Turn the live screen into a numbered list of interactable elements.

This is the piece that makes accurate clicking possible on a small local model.
Rather than asking the model to guess a pixel from raw image bytes (unreliable
below ~7B vision models), we detect the real UI controls and their exact
bounding boxes, then hand the model a compact numbered menu:

    [0] Button "Save"            @ (512, 40)
    [1] Edit   "Search"          @ (300, 80)
    [2] Text   "Untitled - Notepad"

The model only has to answer "click element 0". This is the "Set-of-Marks"
technique and it works with 1.5-3B text models.

Two detectors, tried in order:
  * Windows UI Automation (``uiautomation``): rich, exact, no GPU. Primary.
  * OCR (Windows OCR first, then rapidocr/easyocr): optional fallback for
    controls with no accessible name - canvases, games, icon buttons.

Everything degrades gracefully: if neither is available you still get an empty
element list and the loop can fall back to raw-coordinate actions.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


# UIA control types worth surfacing as actionable/informative to the model.
_INTERACTIVE_ROLES = {
    "Button", "Hyperlink", "MenuItem", "ListItem", "TabItem", "CheckBox",
    "RadioButton", "ComboBox", "Edit", "Document", "TreeItem", "SplitButton",
    "Slider", "MenuBar", "Menu", "Text", "Image", "Custom", "Group",
}
# Roles we keep even when they have no name (they are still clickable targets).
_KEEP_UNNAMED = {"Edit", "Document", "Button", "ComboBox", "Custom"}


@dataclass
class Element:
    id: int
    role: str
    name: str
    bbox: tuple[int, int, int, int]   # (left, top, right, bottom)
    center: tuple[int, int]
    interactive: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def describe(self) -> str:
        name = self.name.strip().replace("\n", " ")
        if len(name) > 60:
            name = name[:57] + "..."
        label = f'"{name}"' if name else "(unlabeled)"
        cx, cy = self.center
        return f'[{self.id}] {self.role:<10} {label} @ ({cx},{cy})'


@dataclass
class Observation:
    """A full snapshot of what Jarvis can see right now."""
    elements: list[Element]
    screen_size: tuple[int, int]
    active_window: str = ""
    screenshot_path: str | None = None

    def menu(self) -> str:
        """The numbered element list shown to the model."""
        if not self.elements:
            return "(no interactable elements detected on screen)"
        return "\n".join(e.describe() for e in self.elements)

    def by_id(self, element_id: int) -> Element | None:
        for e in self.elements:
            if e.id == element_id:
                return e
        return None


def observe(max_elements: int = 60, use_uia: bool = True,
            use_ocr: bool = True) -> Observation:
    """Build an :class:`Observation` of the current desktop."""
    from .screen import screen_size

    size = screen_size()
    active = _active_window_title()
    elements: list[Element] = []

    if use_uia:
        try:
            elements = _detect_uia(max_elements, size, window_title=active)
        except Exception:
            elements = []

    if not elements and use_ocr:
        try:
            elements = _detect_ocr(max_elements)
        except Exception:
            elements = []

    return Observation(elements=elements, screen_size=size, active_window=active)



def _active_window_title() -> str:
    from ..desktop import is_shadow_enabled, get_shadow_manager
    if is_shadow_enabled():
        try:
            windows = get_shadow_manager().list_windows()
            if windows:
                return windows[0].title
            return "Shadow Desktop (Empty)"
        except Exception:
            return "Shadow Desktop (Unavailable)"

    try:
        import pygetwindow as gw  # type: ignore

        w = gw.getActiveWindow()
        return w.title if w else ""
    except Exception:
        return ""


def _title_match(window_title: str, doc_name: str) -> bool:
    """True when the browser Document plausibly belongs to the active tab.

    Window title is usually '<tab title> - <browser>'; the OUTERMOST Document's
    name is the tab title (see :func:`_detect_uia` - an iframe's Document name
    is not). Chromium sometimes serves a STALE tree (previous tab, old
    fullscreen-era coordinates) whose Document name no longer matches - the
    signature of the click-lands-on-the-tab-strip bug.
    """
    import re
    strip = lambda s: re.sub(r"^\(\d+\)\s*", "", (s or "").strip().lower())
    win, doc = strip(window_title), strip(doc_name)
    if not win or not doc:
        return True     # nothing to compare - assume fine
    return win.startswith(doc[:60])


# Window-title/Document-name disagreements already waited out with no change.
#
# The stale-tree retry below is worth paying once per disagreement, but a page
# whose Document can never match its window title pays it on EVERY observation
# for the life of the session: a Discord channel with a YouTube embed reports
# the VIDEO's title ('WALKING STREET IN PATTAYA - YouTube') for a window titled
# 'Discord | @zovexis_ - Comet', and the walk stops at ``max_elements`` before
# it ever reaches the page's own document. Measured: 0.8s of sleep plus a second
# full walk - 1.28s per observe instead of 0.23s, on every step of every task.
#
# Keyed by (window title, outermost document names), so a genuinely different
# stale tab still retries - only the identical disagreement is skipped, and only
# after waiting it out changed nothing. Bounded, and emptied wholesale when full:
# a dropped entry costs one extra retry, never a wrong answer.
_FUTILE_TITLE_RETRY: set[tuple] = set()
_FUTILE_TITLE_RETRY_CAP = 64

# The same policy for the empty-document wait below: some windows keep a
# Document whose content the walk never reaches (a canvas or video surface, or
# a page the element budget caps before its own content), so the 0.6s wait plus
# the second full walk buys nothing on every observation for the life of the
# session. Keyed by (window title, outermost document names), added only after a
# wait that changed nothing, and bounded the same way.
_FUTILE_EMPTY_DOC_RETRY: set[tuple] = set()


def _detect_uia(max_elements: int, size: tuple[int, int],
                window_title: str = "") -> list[Element]:
    """Walk the UI Automation tree of the foreground window or shadow workspace."""
    import os
    from collections import deque

    from ._comtypes_fix import ensure as _ensure_comtypes, data_dir
    _ensure_comtypes()
    import uiautomation as auto  # type: ignore

    # Keep uiautomation's log file out of the user's project directory.
    try:
        auto.Logger.SetLogFile(os.path.join(data_dir(), "uiautomation.log"))
    except Exception:
        pass

    import time

    _MAX_CHROME = 18        # element budget for non-document (window chrome)
    _CHROME_DEPTH = 12
    _DOC_DEPTH = 45         # web pages nest deeply
    _MAX_VISITED = 2500     # hard node cap (each node = several slow COM calls)
    _TIME_BUDGET = 2.5      # wall-clock cap per walk so a huge page can't hang

    sw, sh = size

    from ..desktop import is_shadow_enabled, get_shadow_manager
    if is_shadow_enabled():
        windows = get_shadow_manager().list_windows()
        if not windows:
            return []
        # Errors or a missing root must stay inside the shadow workspace;
        # observe() can return an empty list instead of inspecting the host.
        root = auto.ControlFromHandle(windows[0].hwnd)
    else:
        root = auto.GetForegroundControl()

    if root is None:
        return []



    # Chromium-based browsers build the page's accessibility tree LAZILY: the
    # first UIA query on a freshly focused tab can return an empty Document.
    # If that happens, wait briefly and walk once more.
    pending_retry: tuple | None = None    # disagreement this walk is waiting out
    pending_empty: tuple | None = None    # empty-document wait this walk is paying for
    for attempt in range(2):
        elements: list[Element] = []
        seen: set[tuple] = set()
        centers: set[tuple] = set()
        kept_names: set[str] = set()
        chrome_count = 0
        doc_found = False
        doc_kept = 0
        doc_names: list[str] = []

        # queue holds (control, depth, in_document)
        queue: deque = deque([(root, 0, False)])
        visited = 0
        deadline = time.monotonic() + _TIME_BUDGET
        while (queue and len(elements) < max_elements
               and visited < _MAX_VISITED and time.monotonic() < deadline):
            ctrl, depth, in_doc = queue.popleft()
            visited += 1
            try:
                role = ctrl.ControlTypeName.replace("Control", "")
                rect = ctrl.BoundingRectangle
                left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
            except Exception:
                continue

            is_doc = role == "Document"
            if is_doc:
                doc_found = True
            w, h = right - left, bottom - top
            cx, cy = (left + right) // 2, (top + bottom) // 2
            on_screen = (w > 3 and h > 3 and w < sw and h <= sh
                         and 0 <= cx < sw and 0 <= cy < sh)
            # A node whose (valid) box is entirely outside the viewport - e.g.
            # scrolled below the fold - has all its descendants off-screen too,
            # so we never descend into it. This skips the whole below-the-fold
            # DOM, the main cost on long pages.
            valid_rect = right > left and bottom > top
            fully_offscreen = valid_rect and (right <= 0 or bottom <= 0
                                              or left >= sw or top >= sh)

            # Reject unclickable and surplus chrome nodes BEFORE fetching
            # Name: each property is a cross-process COM call. Still traverse
            # their children, and always name Documents for stale-tab checks.
            keep = role in _INTERACTIVE_ROLES and on_screen
            if not in_doc and not is_doc and chrome_count >= _MAX_CHROME:
                keep = False
            name = ""
            if keep or is_doc:
                try:
                    name = (ctrl.Name or "").strip()
                except Exception:
                    pass
                if is_doc and not in_doc:
                    # Only the OUTERMOST document belongs to the page in the
                    # window title; every other Document is an iframe. Naming
                    # the page from an iframe made a perfectly healthy page look
                    # stale - a Discord channel with a YouTube embed reported
                    # the video's title - so the retry below (a 0.8s sleep plus
                    # a second full walk) fired on every step of every task and
                    # re-walked the same tree forever.
                    doc_names.append(name)

            keep = keep and (name or role in _KEEP_UNNAMED)
            if keep and role == "Text" and name and name in kept_names:
                keep = False    # plain-text duplicate of an element already listed
            key = (role, name, left, top)
            ckey = (cx // 8, cy // 8)
            if keep and key not in seen and ckey not in centers:
                seen.add(key)
                centers.add(ckey)
                if name:
                    kept_names.add(name)
                elements.append(Element(
                    id=len(elements), role=role, name=name,
                    bbox=(left, top, right, bottom), center=(cx, cy),
                ))
                if in_doc:
                    doc_kept += 1
                elif not is_doc:
                    chrome_count += 1

            if len(elements) >= max_elements:
                break  # no children can be used once the element budget is full

            limit = _DOC_DEPTH if (in_doc or is_doc) else _CHROME_DEPTH
            if depth < limit and not fully_offscreen:
                try:
                    children = ctrl.GetChildren()
                except Exception:
                    children = []
                if is_doc or in_doc:
                    # Page content: explore before any remaining chrome so the
                    # budget goes to what the user actually wants to click.
                    for child in reversed(children):
                        queue.appendleft((child, depth + 1, True))
                else:
                    for child in children:
                        queue.append((child, depth + 1, False))

        if attempt == 0 and doc_found and doc_kept == 0:
            key = (window_title, tuple(doc_names))
            if key not in _FUTILE_EMPTY_DOC_RETRY:
                pending_empty = key
                time.sleep(0.6)     # let the renderer finish building the a11y tree
                continue
        if attempt and pending_empty is not None and doc_found and doc_kept == 0:
            # Waiting did not change the answer, so do not wait for it again.
            if len(_FUTILE_EMPTY_DOC_RETRY) >= _FUTILE_TITLE_RETRY_CAP:
                _FUTILE_EMPTY_DOC_RETRY.clear()
            _FUTILE_EMPTY_DOC_RETRY.add(pending_empty)
            pending_empty = None
        # Any outermost document that matches is enough: only a page whose own
        # tree disagrees with its window title is the stale-tree signature.
        mismatch = bool(doc_names) and not any(
            _title_match(window_title, name) for name in doc_names)
        if attempt == 0 and mismatch:
            key = (window_title, tuple(doc_names))
            if key in _FUTILE_TITLE_RETRY:
                break           # this exact disagreement already failed to heal
            # Chromium served a STALE tree (an old tab's page, often with
            # fullscreen-era coordinates) - clicking it hits the tab strip.
            # Give the renderer a moment and walk again.
            pending_retry = key
            time.sleep(0.8)
            continue
        if attempt and pending_retry is not None and mismatch:
            # Waiting did not change the answer, so do not wait for it again.
            if len(_FUTILE_TITLE_RETRY) >= _FUTILE_TITLE_RETRY_CAP:
                _FUTILE_TITLE_RETRY.clear()
            _FUTILE_TITLE_RETRY.add(pending_retry)
        break

    return elements


def _detect_ocr(max_elements: int) -> list[Element]:
    """OCR fallback: every recognised text box becomes a clickable element.

    The engine lives in :mod:`jarvis.perception.ocr`, which probes a chain of
    backends - Windows OCR first, since it needs no model files and runs in
    ~0.7s. This used to depend on easyocr alone; easyocr pulls torch, is often
    absent, and so made the whole fallback silently do nothing.
    """
    from . import ocr as ocr_mod
    from .screen import capture

    shot = capture()
    elements: list[Element] = []
    for box, text, confidence in ocr_mod.read_boxes(shot.image)[:max_elements]:
        if confidence < 0.4 or not text.strip():
            continue
        left, top, right, bottom = box
        elements.append(Element(
            id=len(elements), role="Text", name=text.strip(),
            bbox=(left, top, right, bottom),
            center=((left + right) // 2, (top + bottom) // 2),
        ))
    return elements


def ocr_observation(max_elements: int = 60, use_ocr: bool = True) -> "Observation | None":
    """Read the desktop with OCR alone, for controls the tree cannot name.

    :func:`observe` only reaches OCR when UI Automation returns *nothing*. A
    control with no accessible name is a different failure: the tree answers,
    it simply cannot name the control that was asked for. This gives the
    resolution path a second, pixels-only read to fall back on. Returns
    ``None`` when OCR is unavailable or reads no text, so a caller keeps its
    original error rather than trading a clear message for an empty one.
    """
    if not use_ocr:
        return None
    try:
        from .screen import screen_size

        elements = _detect_ocr(max_elements)
    except Exception:
        return None
    if not elements:
        return None
    return Observation(
        elements=elements,
        screen_size=screen_size(),
        active_window=_active_window_title(),
    )
