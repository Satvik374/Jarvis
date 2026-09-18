"""Tests for the liquid energy core (``blob.js``), the default stage visual.

The blob is a fullscreen SDF shader driven by Jarvis's state, so its failure
modes are silent: a reserved GLSL identifier, a canvas the renderer does not
own, or an inverted smooth-min all render "something" while erasing the blob.
These tests pin the invariants that were expensive to find.
"""

from __future__ import annotations

import re

from jarvis.browser import BrowserRequestHandler, STATIC_DIR

BLOB_JS = (STATIC_DIR / "blob.js").read_text(encoding="utf-8")
APP_JS = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
STYLES_CSS = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
HOLOGRAM_JS = (STATIC_DIR / "hologram.js").read_text(encoding="utf-8")


def _block(source: str, opener: str, closer: str = "};") -> str:
    return source.split(opener, 1)[1].split(closer, 1)[0]


def _fragment_shader() -> str:
    key = "const FRAGMENT_SHADER = `"
    start = BLOB_JS.index(key) + len(key)
    return BLOB_JS[start:BLOB_JS.index("\n  `;", start)]


def test_blob_asset_is_allow_listed_and_loaded_before_the_app():
    assert BrowserRequestHandler._STATIC["/blob.js"] == (
        "blob.js",
        "text/javascript; charset=utf-8",
    )
    assert (STATIC_DIR / "blob.js").is_file()

    # blob.js defines window.FluidBlob, so it must load before app.js uses it.
    blob_tag = INDEX_HTML.index('<script src="/blob.js"></script>')
    app_tag = INDEX_HTML.index('<script src="/app.js"></script>')
    assert blob_tag < app_tag


def test_blob_profiles_cover_every_jarvis_state():
    """A state without a profile silently renders as ``working``."""
    profiles = set(re.findall(r"^\s*([a-z]+):\s*\{", _block(BLOB_JS, "const BLOB_PROFILES = {"), re.MULTILINE))
    states = set(re.findall(r"^\s*([a-z]+):\s*\[", _block(APP_JS, "const stateMeta = {"), re.MULTILINE))
    states |= {"speaking", "muted", "connecting"}

    assert states, "stateMeta parsing failed"
    assert not states - profiles, f"states without a blob profile: {sorted(states - profiles)}"


def test_fragment_shader_avoids_reserved_glsl_identifiers():
    """three.js compiles to GLSL ES 3.0 on WebGL2, where ``active`` is reserved.

    A collision there fails the whole program and the core silently disappears.
    """
    shader = _fragment_shader()
    reserved = ("active", "common", "filter", "interface", "partition", "resource", "typedef")
    for name in reserved:
        assert not re.search(rf"\b(float|vec[234]|int|bool|mat[234])\s+{name}\b", shader), name


def test_smooth_min_is_a_union_not_a_max():
    """The droplets must fuse into the mass instead of being rejected by it.

    Inverting these operands turns the blend into a smooth-max, which removes
    the blob's interior and leaves only a dim halo.
    """
    assert "mix(droplet, d, h)" in BLOB_JS
    assert "(droplet - d)" in BLOB_JS
    assert "(d - droplet)" not in BLOB_JS


def test_canvas_buffer_is_owned_by_the_renderer():
    """Assigning canvas.width by hand then calling setPixelRatio() loses it.

    three re-applies the size it remembers (the 300x150 default) and silently
    overwrites the attribute.
    """
    assert "this.canvas.width =" not in BLOB_JS
    assert "this.canvas.height =" not in BLOB_JS
    assert "this.renderer.setSize(deviceWidth, deviceHeight, false)" in BLOB_JS


def test_blob_is_the_default_stage_mode_and_the_hologram_remains_available():
    assert 'data-holo-mode="blob"' in INDEX_HTML
    assert INDEX_HTML.index('data-holo-mode="blob"') < INDEX_HTML.index('data-holo-mode="hologram"')
    # The active button on load is the blob.
    assert re.search(r'class="holo-btn is-active" data-holo-mode="blob"', INDEX_HTML)
    for mode in ("hologram", "orbit", "wireframe", "quantum"):
        assert f'data-holo-mode="{mode}"' in INDEX_HTML
        assert f'"{mode}"' in APP_JS


def test_stage_controls_route_through_the_core():
    """Mode, reset and pulse must reach the blob as well as the hologram."""
    assert "window.energyCore = orb" in APP_JS
    assert "orb.holo3d" not in APP_JS
    for call in ("orb.setDisplayMode(", "orb.resetView()", "orb.triggerPulse(", "orb.setVoiceLevel("):
        assert call in APP_JS, call
    for method in ("setDisplayMode(", "resetView()", "triggerPulse(", "setVoiceLevel("):
        assert method in APP_JS.split("class EnergyCore")[1], method


def test_blob_hides_the_hologram_canvas_and_pauses_its_loop():
    assert '.core-frame[data-core-mode="blob"] #coreCanvas' in STYLES_CSS
    assert "data-core-mode" in APP_JS
    assert "setSuspended(true)" in APP_JS
    # The hologram must stop rendering, not merely be hidden.
    assert "this.suspended || document.hidden" in HOLOGRAM_JS


def test_blob_layer_sits_below_the_hud_labels():
    """`z-index: 1` on the blob layer used to paint over the centre readout."""
    block = STYLES_CSS.split("#fluidBlob {", 1)[1].split("}", 1)[0]
    assert "z-index: 0" in block
    assert "z-index: 1" not in block


def test_blob_is_responsive_and_respects_reduced_motion():
    # Normalised against the container's short edge, so it keeps its proportions.
    assert "uResolution / shortEdge" in BLOB_JS
    # The render scale adapts when frames get expensive.
    assert "this.renderScale" in BLOB_JS
    assert "adapt(" in BLOB_JS
    # Reduced motion renders a frozen pose rather than a jumpy one.
    assert "this.reducedMotion ? 0 : time" in BLOB_JS
    assert "ResizeObserver" in BLOB_JS
