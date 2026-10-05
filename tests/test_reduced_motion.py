"""The motion preference, and the one stage visual that ignored it.

``aurora.js``, ``blob.js`` and ``grainient.js`` each honoured
``prefers-reduced-motion``; the 3D hologram did not. So a user who had asked
their system for less motion still got a core that never stopped rotating,
rings that kept drifting and a scanning plane sweeping the stage - on the visual
that owns that stage whenever the liquid blob is unavailable, and one click away
when it is not. The preference is only honoured if *every* animated module
honours it, which is what this file pins.

There is no browser in this suite, so the source is read as text. The behaviour
those declarations produce was measured separately, in Chromium with real WebGL:

    ~25 frames in 400ms while animating; exactly 1 frame when the preference is
    switched on and none afterwards; 1 more per state change; the loop resuming
    (26 frames) when it is switched off; and, when the preference is already on
    before the page loads, 1 frame and no queued frame at all.

``tests/test_browser_ui.mjs`` repeats that logic against a stubbed instance
(no WebGL needed) and needs jsdom installed to run.
"""

from __future__ import annotations

from jarvis.browser import STATIC_DIR

HOLOGRAM_JS = (STATIC_DIR / "hologram.js").read_text(encoding="utf-8")
APP_JS = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
STYLES_CSS = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

#: Everything that paints the stage over time. A new module belongs here.
STAGE_VISUALS = ("aurora.js", "blob.js", "grainient.js", "hologram.js")

#: Paths where the user's own input must still draw, even with no loop running.
INTERACTIVE_PATHS = ("setSuspended", "setDisplayMode", "triggerPulse", "setSpeaking")


def _method(name: str, source: str = HOLOGRAM_JS) -> str:
    """The whole body of a method, braces balanced."""
    start = source.index(f"    {name}(")
    open_brace = source.index("{", start)
    depth = 0
    for index in range(open_brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"unbalanced braces in {name}")


def _block(start: int, source: str = STYLES_CSS) -> str:
    """The whole ``@media ... { ... }`` rule beginning at ``start``."""
    open_brace = source.index("{", start)
    depth = 0
    for index in range(open_brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError("unbalanced braces in styles.css")


# --------------------------------------------------------------------------- #
# every animated module, not three of four
# --------------------------------------------------------------------------- #

def test_every_stage_visual_honours_the_motion_preference():
    """The gap this file exists for: one module silently animating on."""
    for name in STAGE_VISUALS:
        source = (STATIC_DIR / name).read_text(encoding="utf-8")
        assert "prefers-reduced-motion" in source or "reducedMotion" in source, (
            f"{name} animates without ever reading the reduced-motion preference"
        )


def test_the_hologram_reads_the_preference_itself():
    """Self-contained, like aurora.js and grainient.js: no wiring required."""
    constructor = _method("constructor")

    assert 'matchMedia("(prefers-reduced-motion: reduce)")' in constructor
    assert "this.reducedMotion = this.motionQuery.matches;" in constructor
    # Also able to follow it live rather than only at construction.
    assert 'this.motionQuery.addEventListener("change"' in _method("bindEvents")


# --------------------------------------------------------------------------- #
# the loop: one settled frame instead of an endless one
# --------------------------------------------------------------------------- #

def test_a_reduced_motion_start_draws_one_frame_and_queues_nothing():
    loop = _method("startLoop")

    guard = loop.index("if (this.reducedMotion)")
    scheduling = loop.index("requestAnimationFrame")
    assert guard < scheduling, "the reduced check must come before the loop is queued"
    assert "this.renderStaticFrame();" in loop[guard:scheduling]

    # The id is tracked, which is what makes the loop stoppable at all.
    assert "this.frameId = requestAnimationFrame(animate);" in loop


def test_the_loop_can_be_stopped_and_resumed():
    stop = _method("stopLoop")
    assert "cancelAnimationFrame(this.frameId)" in stop
    assert "this.frameId = null;" in stop

    toggle = _method("setReducedMotion")
    assert "this.stopLoop();" in toggle and "this.renderStaticFrame();" in toggle
    assert "this.startLoop();" in toggle, "the preference can be turned back off"


def test_the_settled_frame_is_what_the_state_actually_asks_for():
    """A single frame cannot converge a lerp, and must not fake the clock."""
    frame = _method("renderStaticFrame")

    assert "this.currentColor = { ...this.targetColor };" in frame
    # The speech envelope and the shockwaves are positioned against this clock.
    assert "performance.now() / 1000" in frame
    assert "this.renderer.render(this.scene, this.camera);" in frame


def test_nothing_draws_while_the_stage_is_suspended_or_hidden():
    frame = _method("renderStaticFrame")
    assert "if (this.suspended || document.hidden) return;" in frame


def test_autoorbit_drift_is_the_loops_motion_and_is_suppressed():
    transforms = _method("updateTransforms")
    assert "!this.reducedMotion" in transforms, (
        "auto-orbit is the stage moving on its own; it must not drift when "
        "motion is reduced"
    )


# --------------------------------------------------------------------------- #
# a frozen stage still has to report what is happening
# --------------------------------------------------------------------------- #

def test_every_state_changing_path_redraws_a_settled_frame():
    for name in INTERACTIVE_PATHS:
        assert "this.renderIfReduced();" in _method(name), name

    # And that the call actually draws: an empty renderIfReduced would satisfy
    # every assertion above while leaving the stage frozen on a stale frame.
    assert "if (this.reducedMotion) this.renderStaticFrame();" in _method("renderIfReduced")
    assert "this.reducedMotion = next;" in _method("setReducedMotion")


def test_pointer_and_resize_paths_still_draw_for_the_user():
    """Reduced motion means nothing moves on its own - not that input is dead."""
    events = _method("bindEvents")

    assert events.count("this.renderIfReduced();") >= 4, (
        "pointerdown, pointermove, pointerup and wheel each need one"
    )
    assert "this.renderIfReduced();" in events[events.index("new ResizeObserver"):], (
        "with no loop running, a resize leaves the last frame stretched"
    )


def test_the_orb_forwards_a_live_preference_change_to_the_stage():
    handler = APP_JS.index('this.motionQuery.addEventListener("change"')
    forwarded = APP_JS.index("this.holo3d?.setReducedMotion?.(this.reducedMotion);")

    assert forwarded > handler, "the call must be inside the change handler"
    assert forwarded - handler < 400, "the call must be in this handler, not a later one"


# --------------------------------------------------------------------------- #
# the stylesheet half, which is what covers everything that is not a canvas
# --------------------------------------------------------------------------- #

def test_the_blanket_stylesheet_override_is_still_last():
    """Equal specificity means source order decides; a rule added after this
    one would re-enable the transitions it disables."""
    start = STYLES_CSS.index("@media (prefers-reduced-motion: reduce)")
    block = _block(start)

    assert "animation-duration: 0.001ms !important" in block
    assert "transition-duration: 0.001ms !important" in block
    assert "scroll-behavior: auto !important" in block
    assert ".scanlines" in block, "the full-screen scanline overlay is decorative"

    assert STYLES_CSS[start + len(block):].strip() == "", (
        "the reduced-motion block is no longer the last rule in styles.css"
    )
