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

* The stage's decoration is painted *above* the core frame (``--z-stage-deco``
  and ``--z-stage-hud`` exceed ``--z-stage``), and the Live Voice trigger and the
  hologram toolbar live inside that frame. While those layers still took pointer
  events, an invisible radar sweep was the topmost element at the trigger's
  centre and every one of those controls was unclickable - a whole stage of
  controls dead, with nothing on screen looking wrong.

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


# --------------------------------------------------------------------------- #
# stage layering: a decoration must never be the control's click target
# --------------------------------------------------------------------------- #

STAGE_Z_LEVEL = re.compile(r"--z-(stage[a-z-]*):\s*(\d+);")
#: The layers that sit above the core frame and must stay inert.
STAGE_DECORATION = (".stage-grid", ".radar-sweep", ".mode-strip", ".state-readout")


def _flat_rules(source: str = STYLES_CSS) -> dict[str, dict[str, str]]:
    """The *effective* declarations per selector, comments and grouping resolved.

    Grouped selectors are split - the layer's inertness is declared in a shared rule
    with three others - and a selector's several rules are merged, because the
    property that matters is what ends up applied, not which block states it.
    """
    out: dict[str, dict[str, str]] = {}
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}",
                             re.sub(r"/\*.*?\*/", "", source, flags=re.S)):
        body = {part.split(":", 1)[0].strip(): part.split(":", 1)[1].strip()
                for part in match.group(2).split(";") if ":" in part}
        for selector in match.group(1).split(","):
            selector = " ".join(selector.split())
            if selector and not selector.startswith("@"):
                out.setdefault(selector, {}).update(body)
    return out


def test_no_layer_painted_above_the_core_frame_can_swallow_a_click():
    """The stage's decoration outranks the frame, so it must take no clicks.

    ``--z-stage-deco`` and ``--z-stage-hud`` are above ``--z-stage``, the level the
    frame and every control inside it (the Live Voice trigger, the hologram
    toolbar) live on. A layer up there that still takes pointer events is the
    topmost element at a control's centre, so the control never fires - which is
    exactly what an invisible ``.radar-sweep`` did to ``#stageVoiceBtn``. Any
    future layer up there has to be inert for the same reason.
    """
    levels = {name: int(value) for name, value in STAGE_Z_LEVEL.findall(STYLES_CSS)}
    assert "stage" in levels, "--z-stage is gone; the stage's levels moved"
    above = {f"var(--z-{name})" for name, value in levels.items() if value > levels["stage"]}
    assert above, "nothing is painted above --z-stage any more; this gate can go"

    offenders = [f"{selector} (z-index: {decls.get('z-index')})"
                 for selector, decls in _flat_rules().items()
                 if decls.get("z-index") in above and decls.get("pointer-events") != "none"]
    assert offenders == [], (
        "a layer above the core frame takes pointer events, so it can swallow a "
        f"click meant for a control inside it: {offenders}")


def test_the_stage_decorations_and_the_controls_they_cover_are_pinned():
    """Both halves of the fix: the decoration is inert, the control is not."""
    effective = _flat_rules()
    for selector in STAGE_DECORATION:
        assert effective[selector].get("pointer-events") == "none", selector

    trigger = effective[".stage-voice-trigger"]
    assert trigger.get("pointer-events") != "none"
    assert trigger.get("cursor") == "pointer"
    # The frame is a real surface (the blob is dragged from it), so a control
    # inside it is reachable as long as nothing above the frame is hittable.
    frame = effective[".core-frame"]
    assert frame.get("pointer-events") != "none"
    assert frame.get("z-index") == "var(--z-stage)"
