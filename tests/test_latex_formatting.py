"""Terminal LaTeX rendering tests, including the user's expansion example."""

from __future__ import annotations

import contextlib
import io
import re
import unicodedata
import unittest
from unittest import mock

from jarvis.utils import logging as log
from jarvis.utils.latex import render_latex


EXAMPLE = r"""Here is the step-by-step expansion for **$(9x + 5y)^2$** using the algebraic identity:

### **Identity:**
$$(a + b)^2 = a^2 + 2ab + b^2$$

---

### **Solution:**
1. **Identify $a$ and $b$:**
   - $a = 9x$
   - $b = 5y$

2. **Apply the terms to the identity:**
   - $a^2 = (9x)^2 = 81x^2$
   - $2ab = 2 \times (9x) \times (5y) = 90xy$
   - $b^2 = (5y)^2 = 25y^2$

3. **Combine the terms:**
   $$(9x + 5y)^2 = (9x)^2 + 2(9x)(5y) + (5y)^2$$
   $$= 81x^2 + 90xy + 25y^2$$

---

### **Final Answer:**
**$81x^2 + 90xy + 25y^2$**"""


def panel(message: str) -> list[str]:
    stream = io.StringIO()
    with mock.patch.object(log, "_width", return_value=100), \
            contextlib.redirect_stdout(stream):
        log.jarvis(message)
    return [line for line in stream.getvalue().splitlines() if line.strip()]


def plain(message: str) -> str:
    return log._ANSI_RE.sub("", "\n".join(panel(message)))


def visible_width(text: str) -> int:
    total = 0
    for char in log._ANSI_RE.sub("", text):
        if (unicodedata.combining(char)
                or ord(char) in (0xFE0F, 0xFE0E, 0x200D)):
            continue
        wide = (unicodedata.east_asian_width(char) in ("W", "F")
                or 0x1F300 <= ord(char) <= 0x1FAFF
                or 0x2600 <= ord(char) <= 0x27BF)
        total += 2 if wide else 1
    return total


class LatexExampleTests(unittest.TestCase):
    def test_supplied_expansion_is_rendered_not_printed_as_latex(self):
        output = plain(EXAMPLE)

        self.assertNotIn("$$", output)
        self.assertNotIn(r"\times", output)
        self.assertNotIn("**", output)
        self.assertIn("(9x + 5y)²", output)
        self.assertIn("a² + 2ab + b²", output)
        self.assertIn("2 × (9x) × (5y) = 90xy", output)
        self.assertIn("81x² + 90xy + 25y²", output)

    def test_supplied_expansion_keeps_panel_edges_aligned(self):
        widths = {visible_width(line) for line in panel(EXAMPLE)}
        self.assertEqual(len(widths), 1, f"ragged math panel: {widths}")


class LatexParsingTests(unittest.TestCase):
    def test_nested_fraction_root_and_subscript(self):
        self.assertEqual(
            render_latex(r"\frac{1}{\sqrt{x_2}}"),
            "1⁄(√(x₂))",
        )

    def test_commands_are_matched_exactly(self):
        self.assertEqual(
            render_latex(r"A \subset B,\ A \subseteq C"),
            r"A ⊂ B, A ⊆ C",
        )

    def test_unknown_command_is_preserved(self):
        output = render_latex(r"\mystery{x}")
        self.assertIn(r"\mystery", output)
        self.assertIn("x", output)

    def test_ascii_fallback_remains_readable(self):
        self.assertEqual(
            render_latex(
                r"\sqrt{x_2} \times \alpha",
                ascii_only=True,
            ),
            "sqrt(x_2) * alpha",
        )


class LatexDelimiterTests(unittest.TestCase):
    def test_code_currency_shell_and_unmatched_dollars_stay_literal(self):
        output = plain(
            r"Code `$x^2$`; cost \$5; range $5 and $10; "
            r"shell $HOME; unmatched $value; math $x_2$."
        )

        self.assertIn("$x^2$", output)
        self.assertIn("cost $5", output)
        self.assertIn("$5 and $10", output)
        self.assertIn("$HOME", output)
        self.assertIn("$value", output)
        self.assertIn("x₂", output)

    def test_fenced_math_is_literal_code(self):
        output = plain("```text\n$$x^2$$\n```")
        self.assertIn("$$x^2$$", output)
        self.assertNotIn("x²", output)

    def test_multiline_display_math_and_alignment(self):
        output = plain(
            r"""$$
\begin{aligned}
x_1 &= \frac{1}{\sqrt{y_2}} \\
A &\subseteq B
\end{aligned}
$$"""
        )

        self.assertNotIn(r"\begin", output)
        self.assertNotIn("$$", output)
        self.assertIn("x₁", output)
        self.assertIn("1⁄(√(y₂))", output)
        self.assertIn("A", output)
        self.assertIn("⊆ B", output)

    def test_unmatched_display_delimiter_is_not_swallowed(self):
        output = plain("Keep this literal:\n$$x^2\nand the following line")
        self.assertIn("$$x^2", output)
        self.assertIn("following line", output)


if __name__ == "__main__":
    unittest.main()
