import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from jarvis.agent.brain import Brain
from jarvis.agent.loop import _find_image
from jarvis import console


class AsImagesTests(unittest.TestCase):
    def test_none_single_list(self):
        self.assertEqual(Brain._as_images(None), [])
        self.assertEqual(Brain._as_images("img"), ["img"])
        self.assertEqual(Brain._as_images(["a", "b"]), ["a", "b"])


class FindImageTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "shot.png"
        Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_finds_quoted_and_bare_paths(self):
        for prompt in (f'what is in "{self.path}"?',
                       f"describe {self.path} please"):
            img = _find_image(prompt)
            self.assertIsNotNone(img, prompt)
            self.assertEqual(img.size, (4, 4))

    def test_ignores_missing_and_plain_text(self):
        self.assertIsNone(_find_image('open "C:/nope/missing.png"'))
        self.assertIsNone(_find_image("open notepad and type hello"))


class ClipboardPasteTests(unittest.TestCase):
    @staticmethod
    def _read(keys):
        from jarvis.console import _char_input
        chars = list(keys)
        return _char_input("> ", kbhit=lambda: bool(chars),
                           getwch=lambda: chars.pop(0),
                           grace=0.0, echo=lambda s: None,
                           menu=lambda *a: None)

    @patch("jarvis.console._clipboard_to_path", return_value="C:/t/shot.png")
    def test_ctrl_v_inserts_quoted_image_path(self, _cb):
        self.assertEqual(self._read("\x16what is this?\r"),
                         '"C:/t/shot.png" what is this?')

    @patch("jarvis.console._clipboard_to_path", return_value=None)
    def test_ctrl_v_without_image_is_ignored(self, _cb):
        self.assertEqual(self._read("\x16hi\r"), "hi")

    def test_screenshot_clipboard_saved_to_temp_png(self):
        from PIL import Image
        from jarvis import console
        with patch("PIL.ImageGrab.grabclipboard",
                   return_value=Image.new("RGB", (2, 2))):
            path = console._clipboard_to_path()
        try:
            self.assertTrue(path.endswith(".png"))
            self.assertTrue(Path(path).is_file())
        finally:
            Path(path).unlink(missing_ok=True)

    def test_copied_image_file_resolves_to_its_path(self):
        from jarvis import console
        with patch("PIL.ImageGrab.grabclipboard",
                   return_value=[r"C:\pics\shot.jpg", r"C:\docs\a.txt"]):
            self.assertEqual(console._clipboard_to_path(), r"C:\pics\shot.jpg")

    @patch("jarvis.console._clipboard_to_path", return_value=None)
    def test_paste_task_warns_on_empty_clipboard(self, _cb):
        from jarvis import console
        self.assertIsNone(console._paste_task("describe"))

    @patch("jarvis.console._clipboard_to_path", return_value="C:/t/shot.png")
    def test_paste_task_appends_path_to_prompt(self, _cb):
        from jarvis import console
        self.assertEqual(console._paste_task("describe this"),
                         'describe this "C:/t/shot.png"')


class EnhanceTests(unittest.TestCase):
    def _agent(self, reply: str):
        agent = Mock()
        agent.brain.complete.return_value = reply
        return agent

    def test_empty_prompt_is_rejected(self):
        agent = self._agent("")
        self.assertIsNone(console._enhance("", agent))
        agent.brain.complete.assert_not_called()

    @patch("builtins.input", return_value="y")
    def test_json_reply_confirmed_runs(self, _in):
        agent = self._agent('{"prompt": "open notepad and type hello world"}')
        self.assertEqual(console._enhance("notepad hello", agent),
                         "open notepad and type hello world")

    @patch("builtins.input", return_value="n")
    def test_declined_returns_none(self, _in):
        agent = self._agent('{"prompt": "improved"}')
        self.assertIsNone(console._enhance("x", agent))

    @patch("builtins.input", return_value="")
    def test_plain_text_reply_falls_back_to_raw(self, _in):
        agent = self._agent("just an improved prompt, no json")
        self.assertEqual(console._enhance("x", agent),
                         "just an improved prompt, no json")


if __name__ == "__main__":
    unittest.main()
