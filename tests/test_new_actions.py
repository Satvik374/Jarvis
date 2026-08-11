import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from jarvis.agent.loop import Agent
from jarvis.tools import files, system


def _fake_pyautogui():
    """A stand-in pyautogui that records press() calls instead of pressing."""
    mod = types.ModuleType("pyautogui")
    mod.presses = []
    def press(key, presses=1, interval=0.0):
        mod.presses.append((key, presses))
    mod.press = press
    return mod


class HtmlToTextTests(unittest.TestCase):
    def test_strips_non_content_and_collapses_whitespace(self):
        html = ("<html><head><title>T</title><style>p{color:red}</style></head>"
                "<body><script>var x = 1;</script><p>Hello   world</p>"
                "<div>Second line</div></body></html>")
        text = system._html_to_text(html)
        self.assertIn("Hello world", text)
        self.assertIn("Second line", text)
        self.assertNotIn("var x", text)
        self.assertNotIn("color:red", text)


def _response(text="", ctype="text/html", status=200):
    r = Mock()
    r.text = text
    r.status_code = status
    r.headers = {"content-type": ctype}
    return r


class ReadUrlTests(unittest.TestCase):
    def test_empty_url(self):
        self.assertIn("needs a url", system.read_url(""))

    @patch("requests.get")
    def test_html_page_and_auto_scheme(self, get):
        get.return_value = _response("<p>The answer is 42.</p>")
        out = system.read_url("example.com")
        self.assertIn("The answer is 42.", out)
        self.assertEqual(get.call_args[0][0], "https://example.com")

    @patch("requests.get")
    def test_truncation(self, get):
        get.return_value = _response("<p>" + "word " * 5000 + "</p>")
        out = system.read_url("https://example.com", max_chars=500)
        self.assertIn("truncated", out)

    @patch("requests.get")
    def test_http_error(self, get):
        get.return_value = _response("nope", status=404)
        self.assertIn("HTTP 404", system.read_url("https://example.com"))

    @patch("requests.get")
    def test_binary_refused(self, get):
        get.return_value = _response("", ctype="image/png")
        self.assertIn("not a readable text page",
                      system.read_url("https://example.com/x.png"))


class MediaControlTests(unittest.TestCase):
    def test_set_volume_is_deterministic(self):
        pg = _fake_pyautogui()
        with patch.dict(sys.modules, {"pyautogui": pg}):
            out = system.media_control("set_volume", 30)
        self.assertIn("~30%", out)
        # drop to a known zero (50 presses), then raise 15 x 2% = 30%
        self.assertEqual(pg.presses, [("volumedown", 50), ("volumeup", 15)])

    def test_play_pause(self):
        pg = _fake_pyautogui()
        with patch.dict(sys.modules, {"pyautogui": pg}):
            out = system.media_control("play_pause")
        self.assertEqual(pg.presses, [("playpause", 1)])
        self.assertIn("play_pause", out)

    def test_unknown_op(self):
        pg = _fake_pyautogui()
        with patch.dict(sys.modules, {"pyautogui": pg}):
            out = system.media_control("blast_off")
        self.assertIn("unknown media op", out)
        self.assertEqual(pg.presses, [])

    def test_set_volume_needs_value(self):
        pg = _fake_pyautogui()
        with patch.dict(sys.modules, {"pyautogui": pg}):
            out = system.media_control("set_volume")
        self.assertIn("needs 'value'", out)


class NotifyTests(unittest.TestCase):
    def test_empty_message(self):
        self.assertIn("needs a message", system.notify(""))

    @patch("jarvis.tools.system.subprocess.run")
    def test_text_travels_via_env(self, run):
        run.return_value = Mock(returncode=0, stderr="")
        out = system.notify('say "hi" $(rm x)', title="T")
        env = run.call_args.kwargs["env"]
        self.assertEqual(env["JARVIS_N_MSG"], 'say "hi" $(rm x)')
        self.assertEqual(env["JARVIS_N_TITLE"], "T")
        # the script itself never contains the user text
        self.assertNotIn("rm x", run.call_args[0][0][-1])
        self.assertIn("notification shown", out)

    @patch("jarvis.tools.system.subprocess.run")
    def test_failure_reported(self, run):
        run.return_value = Mock(returncode=1, stderr="boom")
        self.assertIn("notification failed", system.notify("hello"))


class FailsafeTests(unittest.TestCase):
    def test_is_failsafe_matches_by_name(self):
        from jarvis.agent.loop import _is_failsafe

        class FailSafeException(Exception):
            pass

        self.assertTrue(_is_failsafe(FailSafeException()))
        self.assertFalse(_is_failsafe(ValueError()))


class FileToolsTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.allow = (str(self.tmp),)
        (self.tmp / "docs").mkdir()
        (self.tmp / "docs" / "my_resume_2026.txt").write_text("cv")
        (self.tmp / "report.pdf").write_text("pdf")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_find_files_substring_and_wildcard(self):
        out = files.find_files("resume", root=str(self.tmp))
        self.assertIn("my_resume_2026.txt", out)
        out = files.find_files("*.pdf", root=str(self.tmp))
        self.assertIn("report.pdf", out)
        self.assertIn("no matches", files.find_files("zzz", root=str(self.tmp)))

    def test_copy_into_folder_and_no_overwrite(self):
        out = files.copy_file(str(self.tmp / "report.pdf"),
                              str(self.tmp / "docs"), allow=self.allow)
        self.assertIn("copied", out)
        self.assertTrue((self.tmp / "docs" / "report.pdf").exists())
        self.assertTrue((self.tmp / "report.pdf").exists())  # source kept
        out = files.copy_file(str(self.tmp / "report.pdf"),
                              str(self.tmp / "docs"), allow=self.allow)
        self.assertIn("already exists", out)

    def test_move_renames_and_removes_source(self):
        out = files.move_file(str(self.tmp / "report.pdf"),
                              str(self.tmp / "old.pdf"), allow=self.allow)
        self.assertIn("moved", out)
        self.assertFalse((self.tmp / "report.pdf").exists())
        self.assertTrue((self.tmp / "old.pdf").exists())

    def test_delete_refuses_outside_sandbox(self):
        # C:/Windows/win.ini exists on every Windows box and is far outside
        # the home-dir sandbox - must refuse before any shell is spawned.
        out = files.delete_path("C:/Windows/win.ini", allow=())
        self.assertIn("refused", out)

    @patch("jarvis.tools.files.subprocess.run")
    def test_delete_sends_to_recycle_bin(self, run):
        victim = self.tmp / "report.pdf"
        def fake_recycle(cmd, env=None, **kw):
            self.assertEqual(env["JARVIS_DEL_PATH"], str(victim))
            victim.unlink()                    # what the shell would do
            return Mock(returncode=0, stderr="")
        run.side_effect = fake_recycle
        out = files.delete_path(str(victim), allow=self.allow)
        self.assertIn("sent to Recycle Bin", out)
        self.assertFalse(victim.exists())


class AskUserTests(unittest.TestCase):
    def test_no_asker_means_no_answer(self):
        self.assertIsNone(Agent._ask_user("which file?", None))

    def test_answer_is_stripped(self):
        self.assertEqual(Agent._ask_user("q", lambda q: "  yes  "), "yes")

    def test_empty_and_erroring_askers_return_none(self):
        self.assertIsNone(Agent._ask_user("q", lambda q: "   "))
        def boom(q):
            raise RuntimeError("mic died")
        self.assertIsNone(Agent._ask_user("q", boom))


class EditFileTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.allow = (str(self.tmp),)
        self.py = self.tmp / "main.py"
        self.py.write_text("DEBUG = True\nx = 1\nx = 1\n", encoding="utf-8")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unique_replace(self):
        out = files.edit_file(str(self.py), "DEBUG = True", "DEBUG = False",
                              allow=self.allow)
        self.assertIn("edited", out)
        self.assertIn("DEBUG = False", self.py.read_text(encoding="utf-8"))

    def test_no_match_refused(self):
        out = files.edit_file(str(self.py), "NOT_IN_FILE", "y",
                              allow=self.allow)
        self.assertIn("no match", out)

    def test_ambiguous_refused(self):
        out = files.edit_file(str(self.py), "x = 1", "x = 2", allow=self.allow)
        self.assertIn("ambiguous", out)
        self.assertIn("x = 1\nx = 1", self.py.read_text(encoding="utf-8"))


class RunCommandCwdTests(unittest.TestCase):
    def test_cwd_is_respected(self):
        import tempfile
        tmp = tempfile.mkdtemp()
        out = system.run_command("cd", cwd=tmp)   # cmd's `cd` prints the cwd
        self.assertIn(Path(tmp).name, out)


class _ScriptedBrain:
    """A brain that replays canned JSON decisions, Claude-Code-test style."""
    def __init__(self, replies):
        self.replies = list(replies)

    def complete(self, system, messages, image=None):
        return self.replies.pop(0)


class CoderLoopTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from jarvis.config import Config
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = Config()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _decision(self, action, args, thought="t"):
        import json
        return json.dumps({"thought": thought, "action": action, "args": args})

    def test_write_run_finish(self):
        from jarvis.agent.coder import Coder
        f = self.tmp / "hello.py"
        brain = _ScriptedBrain([
            self._decision("write_file",
                           {"path": str(f), "content": "print('hi')\n"}),
            self._decision("finish", {"summary": "built hello.py"}),
        ])
        msg, is_ask = Coder(brain, self.cfg).run("make hello", str(self.tmp))
        self.assertEqual(msg, "built hello.py")
        self.assertFalse(is_ask)
        self.assertEqual(f.read_text(encoding="utf-8"), "print('hi')\n")

    def test_desktop_actions_are_blocked_and_recovered(self):
        from jarvis.agent.coder import Coder
        f = self.tmp / "a.txt"
        brain = _ScriptedBrain([
            self._decision("click", {"element": 3}),     # not in coding mode
            self._decision("write_file", {"path": str(f), "content": "ok"}),
            self._decision("finish", {"summary": "done"}),
        ])
        msg, is_ask = Coder(brain, self.cfg).run("task", str(self.tmp))
        self.assertEqual(msg, "done")
        self.assertTrue(f.exists())                       # recovered and built

    def test_ask_bubbles_up(self):
        from jarvis.agent.coder import Coder
        brain = _ScriptedBrain([
            self._decision("ask", {"question": "React or plain HTML?"}),
        ])
        msg, is_ask = Coder(brain, self.cfg).run("build site", str(self.tmp))
        self.assertTrue(is_ask)
        self.assertIn("React", msg)

    def test_coder_caps_its_action_response_budget_without_mutating_config(self):
        from jarvis.agent.coder import Coder, _MAX_CODER_TOKENS

        self.cfg.brain.max_tokens = 500_000
        brain = _ScriptedBrain([])
        brain.cfg = self.cfg.brain

        coder = Coder(brain, self.cfg)

        self.assertEqual(coder.brain.cfg.max_tokens, _MAX_CODER_TOKENS)
        self.assertEqual(self.cfg.brain.max_tokens, 500_000)
        self.assertIsNot(coder.brain.cfg, self.cfg.brain)


class WriteFilesTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from jarvis.config import Config
        from jarvis.perception.elements import Observation
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = Config()
        self.cfg.safety.allow_paths = (str(self.tmp),)
        self.obs = Observation(elements=[], screen_size=(1920, 1080))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_creates_multiple_files_in_one_step(self):
        from jarvis.tools import registry
        r = registry.execute("write_files", {"files": [
            {"path": str(self.tmp / "app" / "index.html"), "content": "<html>"},
            {"path": str(self.tmp / "app" / "app.js"), "content": "let x=1;"},
            {"path": str(self.tmp / "app" / "style.css"), "content": "body{}"},
        ]}, self.obs, self.cfg)
        self.assertTrue(r.ok)
        self.assertEqual((self.tmp / "app" / "app.js").read_text(
            encoding="utf-8"), "let x=1;")
        self.assertEqual(len(list((self.tmp / "app").iterdir())), 3)

    def test_bad_item_is_flagged_without_killing_the_batch(self):
        from jarvis.tools import registry
        r = registry.execute("write_files", {"files": [
            {"content": "no path here"},
            {"path": str(self.tmp / "good.txt"), "content": "ok"},
        ]}, self.obs, self.cfg)
        self.assertFalse(r.ok)                       # batch reported as partial
        self.assertIn("invalid", r.message)
        self.assertTrue((self.tmp / "good.txt").exists())   # good one landed

    def test_empty_list_rejected(self):
        from jarvis.tools import registry
        r = registry.execute("write_files", {"files": []}, self.obs, self.cfg)
        self.assertFalse(r.ok)

    def test_coder_can_use_it(self):
        from jarvis.agent.coder import ALLOWED
        self.assertIn("write_files", ALLOWED)
        self.assertIn("delete_file", ALLOWED)        # delete
        self.assertIn("move_file", ALLOWED)          # rename
        self.assertIn("make_dir", ALLOWED)           # create folders


class SubAgentTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from jarvis.config import Config
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = Config()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_custom_agent_loads_and_filters_desktop_actions(self):
        from jarvis.agent import subagent
        (self.tmp / "agents").mkdir()
        (self.tmp / "agents" / "reviewer.yaml").write_text(
            "name: reviewer\ndescription: reviews text\n"
            "actions: [click, type, read_file]\n"
            "prompt: You are a reviewer.\n", encoding="utf-8")
        agents = subagent.available(root=self.tmp)
        self.assertIn("reviewer", agents)
        acts = agents["reviewer"].actions
        self.assertNotIn("click", acts)          # desktop actions stripped
        self.assertNotIn("type", acts)
        self.assertIn("read_file", acts)
        self.assertIn("finish", acts)            # terminals always granted
        self.assertIn("ask", acts)

    def test_agents_note_lists_all_agents(self):
        from jarvis.agent.subagent import agents_note
        note = agents_note()
        self.assertIn("researcher", note)
        self.assertIn("coder", note)
        self.assertIn("writer", note)            # the sample agents/writer.yaml
        self.assertIn("DELEGATION POLICY", note)
        self.assertIn("bounded specialist task", note)

    def test_main_prompt_prioritises_subagents_for_hard_headless_work(self):
        from jarvis.agent.prompts import build_system_prompt
        prompt = build_system_prompt()
        self.assertIn("DELEGATE EARLY FOR HARD WORK", prompt)
        self.assertIn("use the agent action EARLY", prompt)
        self.assertIn("mouse, keyboard, screen, or", prompt)

    def test_run_agent_blocks_foreign_actions_and_recovers(self):
        import json
        from jarvis.agent import subagent
        brain = _ScriptedBrain([
            json.dumps({"thought": "t", "action": "click",       # real action,
                        "args": {"element": 1}}),                # not allowed
            json.dumps({"thought": "t", "action": "finish",
                        "args": {"summary": "the answer is 42"}}),
        ])
        msg, is_ask = subagent.run_agent(subagent.RESEARCHER, brain,
                                         self.cfg, "q")
        self.assertEqual(msg, "the answer is 42")
        self.assertFalse(is_ask)

    def test_run_agent_ask_bubbles_up(self):
        import json
        from jarvis.agent import subagent
        brain = _ScriptedBrain([
            json.dumps({"thought": "t", "action": "ask",
                        "args": {"question": "which year?"}}),
        ])
        msg, is_ask = subagent.run_agent(subagent.RESEARCHER, brain,
                                         self.cfg, "q")
        self.assertTrue(is_ask)
        self.assertIn("which year", msg)

    def test_unknown_agent_is_rejected_with_the_available_list(self):
        from jarvis.tools import registry
        from jarvis.perception.elements import Observation
        obs = Observation(elements=[], screen_size=(1920, 1080))
        r = registry.execute("agent", {"name": "nope", "task": "x"},
                             obs, self.cfg)
        self.assertFalse(r.ok)
        self.assertIn("unknown agent", r.message)
        self.assertIn("researcher", r.message)


class _FakeWindow:
    def __init__(self, title, hwnd=0):
        self.title = title
        self._hWnd = hwnd
        self.closed = False

    def close(self):
        self.closed = True


class SelfProtectionTests(unittest.TestCase):
    """Jarvis must never close the terminal/process it runs in."""

    # -- run_command guard ------------------------------------------------ #
    def test_threatens_self_detects_image_names(self):
        for cmd in ("taskkill /f /im python.exe",
                    "taskkill /im WindowsTerminal.exe",
                    "Stop-Process -Name powershell -Force",
                    "kill -name pwsh"):
            self.assertTrue(system._threatens_self(cmd), cmd)

    def test_threatens_self_detects_own_pid(self):
        import os
        self.assertTrue(system._threatens_self(f"taskkill /pid {os.getpid()}"))

    def test_harmless_commands_pass(self):
        for cmd in ("taskkill /f /im notepad.exe", "ipconfig",
                    "python kill_test.py", "echo skill issue"):
            self.assertFalse(system._threatens_self(cmd), cmd)

    @patch("jarvis.tools.system.subprocess.run")
    def test_run_command_refuses_without_executing(self, run):
        out = system.run_command("taskkill /f /im python.exe")
        self.assertIn("refused", out)
        run.assert_not_called()

    # -- close_window guard ------------------------------------------------ #
    def test_is_own_console_matches_hwnd_and_title(self):
        from jarvis.tools.apps import _is_own_console
        own = (1234, "JARVIS Console")
        self.assertTrue(_is_own_console(_FakeWindow("anything", 1234), own))
        self.assertTrue(_is_own_console(_FakeWindow("JARVIS Console", 99), own))
        self.assertFalse(_is_own_console(_FakeWindow("Notepad", 99), own))

    def test_close_window_refuses_own_terminal(self):
        from jarvis.tools import apps
        mine = _FakeWindow("JARVIS Console", 1234)
        gw = types.ModuleType("pygetwindow")
        gw.getAllWindows = lambda: [mine]
        with patch.dict(sys.modules, {"pygetwindow": gw}), \
             patch.object(apps, "_own_console_ids",
                          return_value=(1234, "JARVIS Console")):
            out = apps.close_window("JARVIS")
        self.assertIn("refused", out)
        self.assertFalse(mine.closed)

    def test_close_window_still_closes_other_windows(self):
        from jarvis.tools import apps
        other = _FakeWindow("Notepad", 55)
        gw = types.ModuleType("pygetwindow")
        gw.getAllWindows = lambda: [other]
        with patch.dict(sys.modules, {"pygetwindow": gw}), \
             patch.object(apps, "_own_console_ids",
                          return_value=(1234, "JARVIS Console")):
            out = apps.close_window("Notepad")
        self.assertIn("closed", out)
        self.assertTrue(other.closed)

    # -- alt+f4 guard ------------------------------------------------------ #
    def test_alt_f4_refused_when_own_console_is_focused(self):
        from jarvis.tools import apps, keyboard
        with patch.object(apps, "active_is_own_console", return_value=True):
            out = keyboard.press("alt+f4")
        self.assertIn("refused", out)

    def test_other_hotkeys_unaffected_by_guard(self):
        from jarvis.tools import apps, keyboard
        pg = _fake_pyautogui()
        pg.hotkey = lambda *k: pg.presses.append(("hotkey", k))
        pg.size = lambda: (1920, 1080)
        with patch.dict(sys.modules, {"pyautogui": pg}), \
             patch.object(apps, "active_is_own_console", return_value=True):
            out = keyboard.press("ctrl+s")
        self.assertIn("pressed", out)


class CodingRoutingBugTests(unittest.TestCase):
    """Regression tests for the 'everything routes to coding' bug."""

    def _agent(self):
        import tempfile
        from unittest.mock import Mock
        from jarvis.config import Config
        cfg = Config()
        cfg.data.collect_trajectories = False
        cfg.data.trajectory_dir = tempfile.mkdtemp()
        return Agent(Mock(), cfg)

    def test_chat_style_requests_reach_the_chat_classifier(self):
        a = self._agent()
        # everyday 'make/create/fix/generate' phrasing must NOT bypass chat
        for msg in ("make me a healthy meal plan for the week",
                    "create a poem about the sea",
                    "fix my mood with a joke",
                    "generate some baby name ideas"):
            self.assertFalse(a._looks_like_task(msg), msg)

    def test_software_verbs_still_route_to_the_task_loop(self):
        a = self._agent()
        for msg in ("build a snake game", "code a calculator app",
                    "debug the login flow", "refactor main.py",
                    "open notepad", "click the start button"):
            self.assertTrue(a._looks_like_task(msg), msg)

    def test_polite_desktop_commands_skip_the_chat_classifier(self):
        a = self._agent()
        for msg in ("please open notepad",
                    "can you click the start button?",
                    "could you type hello into notepad",
                    "Jarvis, would you launch calculator",
                    "can you scroll down",
                    "will you press enter"):
            self.assertTrue(a._looks_like_task(msg), msg)

        for msg in ("can you explain quantum computing",
                    "could you tell me a joke",
                    "please make me a healthy meal plan",
                    "can you write a poem",
                    "could you play devil's advocate",
                    "can you run through how this works",
                    "would you start by explaining the concept",
                    "can you save me from boredom",
                    "can you develop a meal plan",
                    "could you launch into a quick explanation",
                    "can you press charges",
                    "can you type out a poem",
                    "can you upgrade my workout plan",
                    "can you open with a word of advice",
                    "can you run a test of my knowledge",
                    "can you play a podcast host in this roleplay",
                    "can you build a project plan",
                    "can you please build a small website",
                    "can you type this in French",
                    "can you type a poem in the style of Shakespeare",
                    "can you open your answer with the word hello",
                    "can you open your answer by discussing Python 3.12",
                    "can you minimize the word count",
                    "can you maximize website conversions",
                    "can you develop a game plan",
                    "can you build a software roadmap",
                    "can you run through this script with me",
                    "can you play with this video idea",
                    "can you upgrade this workout program",
                    "can you develop a game plan for my career",
                    "can you implement an exercise program",
                    "can you play a music trivia game with me",
                    "can you open a window into quantum mechanics",
                    "can you save me $5.00",
                    "can you maximize application performance",
                    "can you maximize app engagement",
                    "can you minimize browser tracking",
                    "can you open a window for questions",
                    "can you scroll down memory lane with me"):
            self.assertFalse(a._looks_like_task(msg), msg)

    def test_memory_matches_titles_only(self):
        memory = ("- Learned Task: Build a typing-speed test game\n"
                  "  Successful Plan: Direct Attempt\n"
                  "  Approach: open the editor and write the code\n")
        # exact/fuzzy title hit -> reuse
        self.assertTrue(Agent._memory_has(
            "build a typing-speed test game", memory))
        # substring of the PROSE (not the title) must NOT trigger reuse
        self.assertFalse(Agent._memory_has("open the editor", memory))
        self.assertFalse(Agent._memory_has("write the code", memory))
        self.assertFalse(Agent._memory_has("", memory))


class CharInputTests(unittest.TestCase):
    """The character-level reader: paste absorption, slash menu, Tab."""

    @staticmethod
    def _read(keys, menus=None):
        from jarvis.console import _char_input
        chars = list(keys)
        menu = (lambda *a: None) if menus is None else \
            (lambda m, col: menus.append(m))
        return _char_input("> ", kbhit=lambda: bool(chars),
                           getwch=lambda: chars.pop(0),
                           grace=0.0, echo=lambda s: None, menu=menu)

    def test_typed_single_line(self):
        self.assertEqual(self._read("open notepad\r"), "open notepad")

    def test_paste_with_empty_lines_becomes_one_prompt(self):
        out = self._read("write a story\rabout ai\r\rthe end\r")
        self.assertEqual(out, "write a story\nabout ai\n\nthe end")

    def test_crlf_pairs_do_not_double_break(self):
        self.assertEqual(self._read("a\r\nb\r\nc\r\n"), "a\nb\nc")

    def test_lf_only_paste_breaks_lines(self):
        self.assertEqual(self._read("a\nb\nc\r"), "a\nb\nc")

    def test_backspace_edits_the_current_line(self):
        self.assertEqual(self._read("first\rab\x08c\r"), "first\nac")

    def test_ctrl_c_raises(self):
        with self.assertRaises(KeyboardInterrupt):
            self._read("ab\x03cd")

    def test_slash_pops_menu_and_filters(self):
        menus = []
        self._read("/enh\r", menus)
        shown = [m for m in menus if m]   # drop the clear-on-submit call
        # '/' alone shows the full list; '/enh' narrows it to /enhance.
        self.assertGreater(len(shown[0]), 1)
        self.assertEqual([m[0] for m in shown[-1]], ["/enhance"])

    def test_plain_text_never_draws_menu(self):
        menus = []
        self._read("open notepad\r", menus)
        self.assertEqual(menus, [])

    def test_tab_completes_first_match(self):
        self.assertEqual(self._read("/enh\thello\r"), "/enhance hello")


class TerminalUITests(unittest.TestCase):
    @staticmethod
    def _capture(fn, *a):
        import contextlib
        import io
        import re
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fn(*a)
        # strip ANSI codes so we measure the VISIBLE width
        return [re.sub(r"\033\[[0-9;]*m", "", ln)
                for ln in buf.getvalue().splitlines()]

    def test_jarvis_panel_edges_align(self):
        from jarvis.utils import logging as log
        msg = ("Saved the note.\nSecond line, which is long enough that the "
               "wrapper has to fold it to keep the panel border straight.")
        lines = [ln for ln in self._capture(log.jarvis, msg) if ln]
        self.assertIn("JARVIS", lines[0])
        widths = {len(ln) for ln in lines}
        self.assertEqual(len(widths), 1, f"ragged panel edges: {widths}")

    def test_emit_preserves_newlines(self):
        from jarvis.utils import logging as log
        out = "\n".join(self._capture(log.warn, "first line\nsecond line"))
        self.assertIn("first line", out)
        self.assertIn("second line", out)


class SchemaRegistrySyncTests(unittest.TestCase):
    def test_every_action_has_a_handler(self):
        from jarvis.tools import registry
        from jarvis.tools.schema import ACTIONS_BY_NAME
        self.assertEqual(set(ACTIONS_BY_NAME), set(registry._HANDLERS))


if __name__ == "__main__":
    unittest.main()
