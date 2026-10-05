"""Getting into the interface without tabbing through it first.

The shell puts the brand, the panel toggle and seven more controls in the header
before the composer, so reaching the directive input by keyboard cost nine
stops. The skip link is the standard way around that, and the details that make
it work are the ones pinned here - each of them is a way it silently does
nothing:

* it has to come first in the DOM, or it is not "skip" anything;
* its target has to be able to take focus. The obvious target, ``#promptInput``,
  is ``disabled`` until the terminal link connects and a disabled control can
  never be focused, so that link would appear to do nothing at exactly the
  moment somebody first tries it;
* it has to be out of flow, because the header's measured height is republished
  into ``--header-h``, which every drawer anchors to;
* and it has to stay out of the stage's z-index band, where a layer that takes
  pointer events swallows clicks meant for the controls beneath it (see
  ``test_browser_layout.py``).

The reveal itself (`:focus` on the link, and the ring on the composer it lands
on) could not be observed in the preview browser - that webview never has
document focus, so ``:focus`` cannot match there even when ``activeElement`` is
set. Those two rules are therefore pinned as source here, while the behaviour
that *was* measured end to end in Chromium is: first focusable element, link
activated -> ``activeElement`` is the composer form, header height unchanged,
no horizontal overflow, and the link the topmost element at its own centre.

The second half of the file is about keyboard access to the dialogs. Both the
shortcuts panel and the command palette declare ``aria-modal="true"``, which
tells assistive technology that everything behind them is inert - but nothing
made that true for the keyboard: Tab off the last palette row landed on the
skip link (measured: ``elementFromPoint`` at that control's own centre returned
``#paletteBackdrop``), i.e. focus on a control the user cannot see. The promise
is now kept with ``inert`` plus Tab wrapping, and the palette also points
``aria-activedescendant`` at its highlighted row, without which arrowing
through results announces nothing at all.

The behaviour is driven for real in ``tests/test_browser_ui.mjs`` (jsdom, and
it skips when jsdom is absent), so what is pinned here is the structure that
has to stay true for that behaviour to be possible.
"""

from __future__ import annotations

import re

from jarvis.browser import STATIC_DIR

INDEX_HTML = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
STYLES_CSS = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
APP_JS = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

SKIP_LINK = '<a class="skip-link" href="#composer">'
COMPOSER_TAG = '<form class="composer" id="composer"'
MODAL_DIALOG = re.compile(r'<div class="([a-z-]+)" role="dialog" aria-modal="true"')


def _js_function(name: str) -> str:
    """The body of a top-level function in app.js, up to its closing brace."""
    start = APP_JS.index(f"function {name}(")
    return APP_JS[start : APP_JS.index("\n  }\n", start)]


def _rule(selector: str, source: str = STYLES_CSS) -> str:
    """The declaration block of the first rule whose selector starts with this.

    Written for grouped selectors too (``.skip-link:focus,`` is followed by its
    sibling and only then by the brace).
    """
    match = re.search(rf"{re.escape(selector)}[^{{}};]{{0,120}}\s*\{{", source)
    assert match, f"no rule beginning with {selector!r} in the stylesheet"
    brace = match.end() - 1
    return source[brace + 1 : source.index("}", brace)]


def test_the_skip_link_comes_before_everything_it_skips():
    skip_link = INDEX_HTML.index(SKIP_LINK)

    assert skip_link < INDEX_HTML.index('class="brand"')
    assert skip_link < INDEX_HTML.index("<button")
    # Inside the header, so its containing block is the header box.
    assert skip_link > INDEX_HTML.index('<header class="topbar">')


def test_nothing_focusable_precedes_the_skip_link():
    """The first tab stop is the way past the chrome, not the logo."""
    pattern = re.compile(r"<(?:a|button|input|textarea|select)\b")
    first = min(match.start() for match in pattern.finditer(INDEX_HTML))

    assert first == INDEX_HTML.index(SKIP_LINK)
    zero = INDEX_HTML.find('tabindex="0"')
    assert zero == -1 or zero > first, "a positive tabindex would jump the queue"


def test_the_brand_is_a_label_rather_than_a_dead_link():
    """It was an anchor at an empty fragment: first tab stop, and nowhere to go."""
    assert '<div class="brand">' in INDEX_HTML
    assert '<a class="brand"' not in INDEX_HTML

    start = INDEX_HTML.index('<div class="brand">')
    brand = INDEX_HTML[start : INDEX_HTML.index("</div>", start) + len("</div>")]
    assert "href" not in brand
    assert "tabindex" not in brand, "a label is not a focus stop"
    assert "aria-label" not in brand, "its visible text is already its name"
    # app.js writes the subtitle in remote-agent mode, so the target must survive.
    assert 'id="brandSubtitle"' in brand


def test_no_anchor_in_the_shell_points_at_an_empty_fragment():
    """`href="#"` is a control that appears to work and does nothing."""
    assert 'href="#"' not in INDEX_HTML


def test_it_targets_something_that_can_actually_take_focus():
    assert 'href="#composer"' in INDEX_HTML
    assert COMPOSER_TAG in INDEX_HTML

    # A form is not focusable without this, and the fragment navigation would
    # move the scroll position but leave focus where it was.
    form = INDEX_HTML[INDEX_HTML.index(COMPOSER_TAG):]
    assert form[: len(COMPOSER_TAG) + 20].startswith(COMPOSER_TAG)
    assert 'tabindex="-1"' in form[: len(COMPOSER_TAG) + 20]

    # The reason it is not the textarea: it is disabled until the terminal link
    # connects, and a disabled control can never be focused.
    textarea = INDEX_HTML.index('<textarea id="promptInput"')
    opening_tag = INDEX_HTML[textarea : INDEX_HTML.index(">", textarea)]
    assert "disabled" in opening_tag


def test_it_is_hidden_until_it_is_focused():
    base = _rule(".skip-link")
    assert "opacity: 0;" in base
    assert "translateY(-140%)" in base

    focus = _rule(".skip-link:focus,")
    assert "opacity: 1;" in focus
    assert "translateY(0);" in focus
    # Higher specificity *and* later in the file, so it wins outright.
    assert STYLES_CSS.index(".skip-link:focus,") > STYLES_CSS.index(".skip-link {")


def test_arriving_at_the_composer_is_visible():
    """Moving focus somewhere with nothing to show for it reads as broken."""
    assert "outline: 2px solid var(--accent);" in _rule("#composer:focus")


def test_it_cannot_move_the_header_it_is_anchored_to():
    """`--header-h` is republished from the measured header, drawers depend on it."""
    assert "position: absolute;" in _rule(".skip-link")
    assert "position: relative;" in _rule(".topbar")


def test_it_stays_out_of_the_stage_z_band():
    """Anything painted above the core frame must stay inert, so do not go up there."""
    assert "z-index" not in _rule(".skip-link"), (
        "the skip link must not join the layers above --z-stage; at that level it "
        "would need pointer-events: none to stay click-through, which a link "
        "cannot be"
    )


def test_the_header_still_fits_its_own_rule():
    """A skipped-to composer is no use if the shell overflows to reach it."""
    assert "--header-h: 72px;" in _rule(":root")
    # The narrow header is where a stray in-flow element would do damage.
    narrow = STYLES_CSS.index("@media (max-width: 720px)")
    assert re.search(r"grid-template-rows:\s*auto auto;", STYLES_CSS[narrow:], re.M)


# ---------------------------------------------------------------------------
# Dialogs: aria-modal has to be true for the keyboard, not just for the a11y tree
# ---------------------------------------------------------------------------


def test_every_modal_dialog_is_wired_for_focus_containment():
    """A dialog that claims aria-modal must be one of the ones Tab is trapped in."""
    classes = MODAL_DIALOG.findall(INDEX_HTML)
    assert classes == ["shortcuts", "palette"], f"modal dialogs changed: {classes}"

    # Derived from the markup, so adding a third aria-modal dialog fails here
    # until it is added to the containment list too.
    expected = "[{}].forEach".format(", ".join(f'".{name}"' for name in classes))
    assert expected in APP_JS, f"app.js does not contain {expected}"


def test_the_page_behind_a_dialog_is_really_inert():
    """`aria-modal` is advisory; `inert` is what a browser actually enforces."""
    assert "layer.inert = true;" in APP_JS
    assert "layer.inert = wasInert;" in APP_JS

    layers = _js_function("modalLayers")
    # The whole interactive page, not just the stage: the header controls and
    # both drawers are behind the dialog too.
    assert '".app-shell"' in layers
    assert "elements.terminalDrawer" in layers
    assert "elements.sessionsDrawer" in layers


def test_inerting_the_page_is_paired_with_undoing_it():
    """An unbalanced pair would leave the interface permanently dead."""
    for backdrop in ("shortcutsBackdrop", "paletteBackdrop"):
        assert f"pushModal(elements.{backdrop})" in APP_JS
    assert APP_JS.count("pushModal(") == APP_JS.count("popModal(")
    # Only on the state change, so a repeated open cannot stack a second entry.
    assert APP_JS.count("if (!wasOpen)") >= 2
    assert APP_JS.count("if (wasOpen) popModal();") == 2


def test_tab_wraps_at_the_ends_of_a_dialog():
    """Wrapping is what keeps focus off the controls under the backdrop."""
    contain = _js_function("containFocus")
    assert 'event.key !== "Tab"' in contain
    assert "!root.contains(active)" in contain, "focus starting outside must be pulled back in"
    assert "event.preventDefault();" in contain
    for name in ("first", "last"):
        assert f"{name}.focus();" in contain


def test_the_listbox_tells_the_combobox_which_row_is_active():
    """Without aria-activedescendant, arrowing through results announces nothing."""
    assert 'elements.paletteInput.setAttribute("aria-activedescendant", selected.id)' in APP_JS
    assert 'elements.paletteInput.removeAttribute("aria-activedescendant")' in APP_JS
    assert "button.id = `palette-option-${index}`;" in APP_JS, "the pointer needs a target"
    # A listbox may only own options and groups, so the caption is decoration.
    assert 'heading.setAttribute("role", "presentation");' in APP_JS
    # Every path that moves the highlight has to move the pointer with it.
    for name in ("renderPalette", "movePalette"):
        assert "syncActiveOption();" in _js_function(name), f"{name} does not sync the pointer"


def test_closing_a_dialog_cannot_drop_focus_on_the_body():
    """`document.body` reads as "no focus", and the next Tab restarts at the top."""
    restore = _js_function("restoreFocus")
    assert "node !== document.body" in restore
    assert 'node.closest("[inert]")' in restore, "aiming at an inert node focuses nothing"
    assert 'node.closest("[hidden]")' in restore, "a closed dialog is not a resting place"
    # Both close paths go through it, including when the composer cannot take
    # focus yet (it is disabled until the terminal link connects).
    assert "restoreFocus(focusBeforeShortcuts);" in APP_JS
    assert "restoreFocus(elements.prompt.disabled ? focusBeforePalette : elements.prompt);" in APP_JS
