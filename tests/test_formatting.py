"""Tests for markdown rendering in the JARVIS reply panel.

The panel is drawn by hand, so the thing most worth pinning is that styling
never breaks the box: every row has to end up the same printable width no
matter what escapes, emoji or long words went into it.
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.utils import logging as log            # noqa: E402

SHIELD = chr(0x1F6E1) + chr(0xFE0F)   # emoji + variation selector
# A BARE emoji is the one that actually catches a width bug: len() counts it
# as one column, the terminal draws two. With a variation selector the naive
# count coincidentally lands on 2 (char + selector) and hides the bug.
LAPTOP = chr(0x1F4BB)


def panel(msg: str) -> list[str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        log.jarvis(msg)
    return [row for row in buf.getvalue().splitlines() if row.strip()]


def plain(msg: str) -> str:
    return log._ANSI_RE.sub("", "\n".join(panel(msg)))


def oracle_width(s: str) -> int:
    """An INDEPENDENT width measure, deliberately not log._vislen.

    Measuring the panel with the same function the panel was built from is
    circular - the box would look aligned to the test even with the width bug
    that prompted all this still in place.
    """
    import unicodedata
    total = 0
    for ch in re.sub(r"\x1b\[[0-9;]*m", "", s):
        if unicodedata.combining(ch) or ord(ch) in (0xFE0F, 0xFE0E, 0x200D):
            continue
        wide = (unicodedata.east_asian_width(ch) in ("W", "F")
                or 0x1F300 <= ord(ch) <= 0x1FAFF
                or 0x2600 <= ord(ch) <= 0x27BF)
        total += 2 if wide else 1
    return total


class TestVisibleWidth(unittest.TestCase):
    def test_ansi_escapes_are_free(self):
        self.assertEqual(log._vislen("\033[1mhi\033[0m"), 2)

    def test_plain_ascii_matches_len(self):
        self.assertEqual(log._vislen("hello world"), 11)

    def test_bare_emoji_takes_two_columns(self):
        self.assertEqual(log._vislen(LAPTOP), 2)
        self.assertNotEqual(log._vislen(LAPTOP), len(LAPTOP))   # not just len()

    def test_emoji_with_variation_selector_takes_two_columns(self):
        self.assertEqual(log._vislen(SHIELD), 2)

    def test_cjk_takes_two_columns(self):
        self.assertEqual(log._vislen("日本"), 4)

    def test_combining_marks_are_free(self):
        self.assertEqual(log._vislen("é"), 1)


class TestPanelAlignment(unittest.TestCase):
    """The regression that started this: emoji headings pushed the right
    border out, because len() counts them as one column."""

    def _assert_aligned(self, msg):
        widths = {oracle_width(r) for r in panel(msg)}
        self.assertEqual(len(widths), 1, f"ragged panel: widths={widths}")

    def test_bare_emoji_heading_stays_aligned(self):
        self._assert_aligned(f"### {LAPTOP} GitHub & Development\n* **a**: b")

    def test_variation_selector_emoji_heading_stays_aligned(self):
        self._assert_aligned(f"### {SHIELD} Security & Accounts\n* **a**: b")

    def test_plain_text_stays_aligned(self):
        self._assert_aligned("just a sentence")

    def test_long_wrapped_bullets_stay_aligned(self):
        self._assert_aligned("* " + "word " * 60)

    def test_unbroken_long_word_stays_aligned(self):
        self._assert_aligned("see https://example.com/" + "x" * 200)

    def test_code_span_stays_aligned(self):
        self._assert_aligned("run `some-fairly-long-command --flag` now")

    def test_empty_message_stays_aligned(self):
        self._assert_aligned("")

    def test_cjk_stays_aligned(self):
        self._assert_aligned("* 日本語のテキストです " * 8)


class TestMarkersAreRendered(unittest.TestCase):
    def test_bold_markers_are_gone_but_text_remains(self):
        out = plain("**Google**: alert")
        self.assertNotIn("**", out)
        self.assertIn("Google", out)

    def test_heading_markers_are_gone(self):
        out = plain("### Security & Accounts")
        self.assertNotIn("#", out)
        self.assertIn("Security & Accounts", out)

    def test_bullets_become_dots(self):
        out = plain("* first\n- second\n+ third")
        self.assertNotRegex(out, r"^\s*[-*+]\s", )
        self.assertEqual(out.count("•"), 3)

    def test_backticks_are_gone_but_code_remains(self):
        out = plain("edit `Jarvis-Fable` now")
        self.assertNotIn("`", out)
        self.assertIn("Jarvis-Fable", out)

    def test_italic_markers_are_gone(self):
        out = plain('titled *"Build a Second Brain"*')
        self.assertNotIn("*", out)
        self.assertIn("Build a Second Brain", out)

    def test_numbered_list_keeps_its_number(self):
        out = plain("1. first\n2. second")
        self.assertIn("1.", out)
        self.assertIn("second", out)

    def test_blockquote_keeps_its_text(self):
        self.assertIn("quoted thing", plain("> quoted thing"))

    def test_link_keeps_text_and_url(self):
        out = plain("see [the PR](https://github.com/x/y/pull/3)")
        self.assertIn("the PR", out)
        self.assertIn("github.com/x/y/pull/3", out)
        self.assertNotIn("](", out)

    def test_horizontal_rule_becomes_a_divider(self):
        self.assertNotIn("---", plain("a\n\n---\n\nb"))

    def test_bare_asterisk_in_prose_is_left_alone(self):
        self.assertIn("2 * 3 = 6", plain("2 * 3 = 6"))


class TestCodeFences(unittest.TestCase):
    def test_fence_markers_are_dropped_and_body_kept(self):
        out = plain("try:\n```bash\npip install foo\n```\ndone")
        self.assertNotIn("```", out)
        self.assertIn("pip install foo", out)

    def test_markdown_inside_a_code_span_is_not_styled(self):
        # backticks win: **x** inside them is literal text, not bold
        self.assertIn("**x**", plain("literal `**x**` here"))


class TestEscapeHygiene(unittest.TestCase):
    def test_styling_survives_a_wrap(self):
        """A bold span long enough to wrap must still be bold on the rows
        after the break - that is what styling per word buys us."""
        rows = panel("**" + "boldword " * 40 + "**")
        body = [r for r in rows if "boldword" in r]
        self.assertGreater(len(body), 1, "expected the bold run to wrap")
        for i, row in enumerate(body[1:], start=1):
            self.assertIn("\033[1m", row,
                          f"row {i} lost its bold across the wrap")

    def test_every_row_closes_its_styling_before_the_border(self):
        for row in panel("* **bold** and `code` " + "pad " * 30):
            content = row[:row.rindex("\033[38;5;45m")]     # drop right border
            last_reset = content.rfind("\033[0m")
            self.assertNotIn("\033[", content[last_reset + 4:],
                             "an escape is opened after the final reset")

    def test_wrapped_bullet_continuation_is_indented(self):
        rows = [log._ANSI_RE.sub("", r) for r in panel("* " + "word " * 40)]
        body = [r for r in rows if "word" in r]
        self.assertGreater(len(body), 1, "expected the bullet to wrap")
        self.assertTrue(body[1].startswith("│   word"),
                        f"continuation not hanging-indented: {body[1]!r}")

    def test_nested_bullet_keeps_its_indent(self):
        rows = [log._ANSI_RE.sub("", r) for r in panel("* parent\n  * child")]
        child = [r for r in rows if "child" in r][0]
        self.assertTrue(child.startswith("│   • child"),
                        f"nested bullet lost its indent: {child!r}")

    def test_link_text_is_separated_from_its_url(self):
        self.assertIn("the PR (https://", plain("[the PR](https://x.io/1)"))


class TestWidthOracleAgrees(unittest.TestCase):
    """_vislen and the test's independent oracle must agree; if they drift,
    one of them is wrong and every alignment test above is suspect."""

    def test_agreement_on_representative_strings(self):
        for sample in ("plain", SHIELD, LAPTOP, "日本語", "é", "\033[1mx\033[0m",
                       f"### {LAPTOP} Security", ""):
            self.assertEqual(log._vislen(sample), oracle_width(sample),
                             f"width disagreement on {sample!r}")


class TestColourlessTerminal(unittest.TestCase):
    """Legacy conhost blanks every colour. Markers must still be stripped -
    otherwise the fallback is strictly worse than doing nothing."""

    def test_markers_still_stripped_without_ansi(self):
        blanked = {k: "" for k in log._COLORS}
        with mock.patch.dict(log._COLORS, blanked):
            out = "\n".join(panel("### Head\n* **bold** and `code`"))
        self.assertNotIn("**", out)
        self.assertNotIn("###", out)
        self.assertNotIn("`", out)
        self.assertIn("bold", out)
        self.assertIn("code", out)


if __name__ == "__main__":
    unittest.main()
