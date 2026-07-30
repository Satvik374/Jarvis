"""Cursor movement and mid-line editing in the console's character reader.

The reader is hand-rolled (it has to be, for the live '/' menu and one-shot
paste capture), so every key that a normal ``input()`` would give us for free
has to be implemented and pinned here.

Two things get asserted: the returned STRING (did the edit land in the right
place) and, for the moves, the ECHO (did the terminal cursor actually go
where the buffer thinks it did).
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.console import _char_input            # noqa: E402

# msvcrt reports these as a two-character sequence: a \xe0 lead-in, then a code.
LEFT = "\xe0K"
RIGHT = "\xe0M"
CTRL_LEFT = "\xe0s"
CTRL_RIGHT = "\xe0t"
HOME = "\xe0G"
END = "\xe0O"
DELETE = "\xe0S"
UP = "\xe0H"
BACKSPACE = "\x08"


def read(keys: str) -> str:
    chars = list(keys)
    return _char_input("> ", kbhit=lambda: bool(chars),
                       getwch=lambda: chars.pop(0),
                       grace=0.0, echo=lambda s: None,
                       menu=lambda *a: None)


def read_with_echo(keys: str) -> tuple[str, str]:
    chars = list(keys)
    out: list[str] = []
    value = _char_input("> ", kbhit=lambda: bool(chars),
                        getwch=lambda: chars.pop(0),
                        grace=0.0, echo=out.append,
                        menu=lambda *a: None)
    return value, "".join(out)


class TestArrowMovement(unittest.TestCase):
    """The reported bug: arrows were consumed and thrown away, so the cursor
    could not move and every fix meant deleting back to the mistake."""

    def test_left_then_type_inserts_mid_word(self):
        self.assertEqual(read("hllo" + LEFT * 3 + "e" + "\r"), "hello")

    def test_right_moves_back_toward_the_end(self):
        self.assertEqual(read("helo" + LEFT * 2 + RIGHT + "l" + "\r"), "hello")

    def test_left_stops_at_the_start_of_the_line(self):
        self.assertEqual(read("abc" + LEFT * 99 + "X" + "\r"), "Xabc")

    def test_right_stops_at_the_end_of_the_line(self):
        self.assertEqual(read("abc" + RIGHT * 99 + "X" + "\r"), "abcX")

    def test_left_emits_a_backspace_so_the_terminal_cursor_moves(self):
        _, echoed = read_with_echo("ab" + LEFT + "\r")
        self.assertIn("\b", echoed)

    def test_arrows_do_not_end_up_in_the_text(self):
        value = read("hi" + LEFT + RIGHT + UP + "\r")
        self.assertEqual(value, "hi")


class TestWordMovement(unittest.TestCase):
    def test_ctrl_left_jumps_to_the_start_of_the_word(self):
        self.assertEqual(read("one two" + CTRL_LEFT + "X" + "\r"), "one Xtwo")

    def test_ctrl_left_twice_crosses_two_words(self):
        self.assertEqual(read("one two" + CTRL_LEFT * 2 + "X" + "\r"),
                         "Xone two")

    def test_ctrl_right_returns_to_the_end(self):
        self.assertEqual(read("one two" + HOME + CTRL_RIGHT + "X" + "\r"),
                         "oneX two")

    def test_ctrl_left_clamps_at_the_start(self):
        self.assertEqual(read("one" + CTRL_LEFT * 9 + "X" + "\r"), "Xone")


class TestHomeEndDelete(unittest.TestCase):
    def test_home_goes_to_the_start(self):
        self.assertEqual(read("world" + HOME + "hello " + "\r"), "hello world")

    def test_end_goes_to_the_end(self):
        self.assertEqual(read("hell" + HOME + END + "o" + "\r"), "hello")

    def test_delete_removes_the_character_under_the_cursor(self):
        self.assertEqual(read("hexllo" + HOME + RIGHT * 2 + DELETE + "\r"),
                         "hello")

    def test_delete_at_the_end_does_nothing(self):
        self.assertEqual(read("hi" + DELETE + "\r"), "hi")


class TestBackspaceRespectsTheCursor(unittest.TestCase):
    """Backspace used to pop the last character no matter where the cursor
    was, so fixing a typo mid-line silently ate the wrong one."""

    def test_backspace_deletes_before_the_cursor_not_at_the_end(self):
        self.assertEqual(read("axbc" + LEFT * 2 + BACKSPACE + "\r"), "abc")

    def test_backspace_at_the_start_is_a_no_op(self):
        self.assertEqual(read("abc" + HOME + BACKSPACE + "\r"), "abc")

    def test_backspace_still_works_at_the_end(self):
        self.assertEqual(read("abcx" + BACKSPACE + "\r"), "abc")

    def test_backspace_repaints_the_tail(self):
        _, echoed = read_with_echo("axbc" + LEFT * 2 + BACKSPACE + "\r")
        self.assertIn("bc", echoed.replace("\b", ""))


class TestExistingBehaviourStillWorks(unittest.TestCase):
    def test_plain_line_is_unchanged(self):
        self.assertEqual(read("open notepad\r"), "open notepad")

    def test_multiline_paste_is_still_one_prompt(self):
        self.assertEqual(read("first\rsecond\r"), "first\nsecond")

    def test_cursor_resets_between_pasted_lines(self):
        # after the newline the cursor must be at column 0 of the new line,
        # not still pointing into the previous one
        self.assertEqual(read("ab\rcd" + HOME + "X" + "\r"), "ab\nXcd")

    def test_tab_completes_a_slash_command(self):
        self.assertEqual(read("/vo\t\r"), "/voice ")

    def test_tab_completes_from_the_end_even_after_moving(self):
        self.assertEqual(read("/vo" + LEFT + "\t\r"), "/voice ")


class TestTrailingNewlineInAPaste(unittest.TestCase):
    """The reported bug: pasting text that ends with a newline submitted the
    prompt instantly, so you could never type after a paste. Pasted keys are
    already queued (getwch returns at once); a typed key is waited for."""

    def read_timed(self, pasted: str, typed: str) -> str:
        chars = list(pasted + typed)
        queued = len(pasted)

        def getwch() -> str:
            nonlocal queued
            if queued > 0:
                queued -= 1
            else:
                time.sleep(0.03)        # a human deciding what to press
            return chars.pop(0)

        return _char_input("> ", kbhit=lambda: queued > 0, getwch=getwch,
                           grace=0.0001, echo=lambda s: None,
                           menu=lambda *a: None)

    def test_paste_ending_in_newline_does_not_submit(self):
        self.assertEqual(self.read_timed("hello\r", "world\r"), "hello\nworld")

    def test_blank_line_inside_a_paste_does_not_submit(self):
        self.assertEqual(self.read_timed("a\r\rb\r", "\r"), "a\n\nb\n")

    def test_typed_enter_still_submits(self):
        self.assertEqual(self.read_timed("", "hi\r"), "hi")


class TestMenuFollowsTheCursor(unittest.TestCase):
    """_draw_menu re-homes the cursor with an ABSOLUTE column, so if the menu
    kept using len(buf) it would drag the cursor back to the end of the line
    on every arrow press - the bug would survive in slash-command lines."""

    def _columns(self, keys: str) -> list[int]:
        chars = list(keys)
        cols: list[int] = []
        _char_input("> ", kbhit=lambda: bool(chars),
                    getwch=lambda: chars.pop(0), grace=0.0,
                    echo=lambda s: None,
                    menu=lambda matches, col: cols.append(col))
        return cols

    def test_menu_column_moves_back_with_the_cursor(self):
        # The final call is the clear-on-submit (a fixed column 1), not a
        # cursor position - drop it before comparing.
        cols = self._columns("/voice" + LEFT * 3 + "\r")[:-1]
        at_end, after_three_lefts = cols[-4], cols[-1]
        self.assertEqual(after_three_lefts, at_end - 3,
                         f"menu column did not track the cursor: {cols}")


if __name__ == "__main__":
    unittest.main()
