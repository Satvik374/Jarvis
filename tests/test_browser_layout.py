"""Layout invariants for the browser interface's shell.

Two failures live here, and both are invisible on a wide desktop window while
being severe on a narrow one:

* The header holds a brand plus eight action controls with a min-content width
  around 454px (about 918px once the reconnect control appears). Grid items
  default to ``min-width: auto``, so an unscoped track let the topbar widen the
  shell past the viewport; ``body { overflow: hidden }`` then clipped the
  right-hand controls - connection pill, ``END SESSION`` - into unreachability.
  The fix is a ``minmax(0, 1fr)`` track (the shell can no longer be widened)
  plus a two-row header below the width where one row cannot fit.

* ``--header-h`` is the single source of truth for the header's height and every
  drawer top. It used to be repeated as literals in five places, so raising it
  for the two-row header left the drawers sitting across it.

There is no browser in the test suite, so the stylesheet is read as text: these
pin the declarations and, more importantly, their *order*. Equal specificity
means source order decides, and a media query placed before the rule it
overrides silently loses - which is how the first attempt at this fix failed.
The pixel behaviour was measured separately, in Chromium, from 320px to 1600px.
"""

from __future__ import annotations

import re

from jarvis.browser import STATIC_DIR

STYLES_CSS = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
APP_JS = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

NARROW_HEADER_MARKER = "Narrow header: two rows instead of an overflowing one"


def _rule(selector: str, source: str = STYLES_CSS) -> str:
    """Return the declaration block of the first ``selector { ... }``."""
    opener = f"{selector} {{"
    start = source.index(opener) + len(opener)
    return source[start : source.index("}", start)]


def _media_block(start: int) -> str:
    """Return the whole ``@media ... { ... }`` rule beginning at ``start``."""
    open_brace = STYLES_CSS.index("{", start)
    depth = 0
    for index in range(open_brace, len(STYLES_CSS)):
        if STYLES_CSS[index] == "{":
            depth += 1
        elif STYLES_CSS[index] == "}":
            depth -= 1
            if depth == 0:
                return STYLES_CSS[start : index + 1]
    raise AssertionError("unbalanced braces in styles.css")


def _enclosing_media(index: int, pattern: str = "@media") -> str:
    """Return the media block that declares whatever lives at ``index``."""
    start = STYLES_CSS.rindex(pattern, 0, index)
    block = _media_block(start)
    assert start <= index < start + len(block), "declaration sits in another block"
    return block


def _narrow_block() -> str:
    marker = STYLES_CSS.index(NARROW_HEADER_MARKER)
    start = STYLES_CSS.index("@media (max-width: 720px)", marker)
    return _media_block(start)


def test_shell_track_cannot_be_widened_by_a_wide_child():
    assert "grid-template-columns: minmax(0, 1fr);" in _rule(".app-shell")


def test_header_row_may_grow_when_the_action_row_wraps():
    """A fixed row clips the wrapped line; an auto maximum lets the header grow."""
    assert "grid-template-rows: minmax(var(--header-h), auto)" in _rule(".app-shell")


def test_header_height_is_a_single_variable():
    assert "--header-h: 72px;" in _rule(":root")

    assert "--header-h: 88px;" in _rule(":root", _narrow_block())
    # The tablet breakpoint lowers it again for the single-row header.
    tablet = _enclosing_media(STYLES_CSS.index("--header-h: 62px;"))
    assert "max-width: 900px" in tablet


def test_drawers_are_anchored_to_the_header_variable():
    for selector in (
        ".sessions-drawer",
        ".terminal-drawer",
    ):
        assert "var(--header-h)" in _rule(selector), selector

    # Every drawer top, including the stacked media-query overrides.
    tops = re.findall(r"\.activity-panel \{\s*position: fixed;\s*top: ([^;]+);", STYLES_CSS)
    assert tops, "no fixed activity-panel top declarations found"
    assert all("var(--header-h)" in top for top in tops), tops


def test_narrow_header_is_two_rows_and_declared_last():
    block = _narrow_block()

    assert "grid-template-columns: minmax(0, 1fr);" in _rule(".topbar", block)
    assert "grid-template-rows: auto auto;" in _rule(".topbar", block)


def test_narrow_header_overrides_come_after_the_rules_they_replace():
    """A media query declared earlier in the file loses to the later base rule."""
    marker = STYLES_CSS.index(NARROW_HEADER_MARKER)

    for selector in (".topbar {", ".app-shell {", ".sessions-drawer {", ".terminal-drawer {"):
        assert marker > STYLES_CSS.index(selector), f"{selector} is declared after the override"

    # Same specificity as its earlier media-query twins, so only source order
    # separates them - the drawer top must be the one that wins.
    assert marker > STYLES_CSS[:marker].rindex("body.panel-open .workspace .activity-panel")


def test_narrow_breakpoint_is_wide_enough_for_the_widest_header():
    """One row needs room for the brand plus every control, reconnect included."""
    assert "@media (max-width: 720px)" in _narrow_block()
    # Measured: without dropping the decorative centre readout the header still
    # overflowed between 901px and ~917px, so it goes at 1100px.
    center = _media_block(STYLES_CSS.index("@media (max-width: 1100px)"))
    assert "display: none;" in _rule(".topbar-center", center)


def test_palette_button_keeps_its_hint_inside_the_button():
    """Two grid rows made the `CTRL K` hint hang 12px below the 32px button."""
    rule = _rule(".palette-button")
    assert "grid-auto-flow: column;" in rule
    assert "width: auto;" in rule


def test_page_publishes_the_measured_header_height():
    assert "syncHeaderHeight" in APP_JS
    assert 'document.documentElement.style.setProperty("--header-h"' in APP_JS
    # jsdom has no ResizeObserver, so the observation has to be optional.
    assert 'typeof ResizeObserver === "function"' in APP_JS


def test_panel_hiding_rule_outranks_the_base_display_declaration():
    """The drawer breakpoint must beat the base ``display: flex`` declared later."""
    hidden = STYLES_CSS.index(".workspace .activity-panel {")
    base_rules = [m.start() for m in re.finditer(r"^\.activity-panel \{", STYLES_CSS, re.MULTILINE)]

    assert base_rules, "no base .activity-panel rule found"
    assert hidden > base_rules[-1]
    assert "display: none;" in _rule(".workspace .activity-panel")
