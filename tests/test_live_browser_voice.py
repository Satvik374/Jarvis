"""Tests for In-Browser Real-Time Voice Mode (ChatGPT / Gemini style)."""

import os
import pathlib
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from jarvis.browser import BrowserRequestHandler
from jarvis.live.speculative import FastFillerEngine
from jarvis.live.supervisor import LiveVoiceSupervisor
from jarvis.config import Config


class TestLiveBrowserVoice(unittest.TestCase):
    def setUp(self):
        # Never touch the user's shared live flag or physical audio devices.
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch("jarvis.utils.voice.tempfile.gettempdir", return_value=directory))
        self.enterContext(patch("jarvis.utils.voice._live_mode_active", False))
        self.enterContext(patch("jarvis.utils.voice.interrupt_speech"))
        self.enterContext(patch("jarvis.utils.voice.configure"))
        self.enterContext(patch.dict(os.environ, {"JARVIS_LIVE_MODE": "0"}))

    def test_browser_worker_live_mode_can_be_disabled_after_startup(self):
        from jarvis.browser import TerminalBridge
        from jarvis.utils import voice

        for initial_active in (False, True):
            with self.subTest(initial_active=initial_active):
                bridge = TerminalBridge(token="testtoken", live_voice=initial_active)
                # An inherited force-on flag must not override browser toggles.
                with patch.dict(os.environ, {"JARVIS_LIVE_MODE": "1"}), \
                     patch("jarvis.browser.subprocess.Popen") as popen, \
                     patch("jarvis.browser.threading.Thread"):
                    bridge.start()
                    child_env = popen.call_args.kwargs["env"]

                for active in (True, False, True, False):
                    bridge.set_live_voice_active(active)
                    # Child has its own globals and only startup environment.
                    with patch.dict(os.environ, child_env, clear=True), \
                         patch.object(voice, "_live_mode_active", False):
                        self.assertEqual(voice.is_live_mode_active(), active)

    def test_permissions_policy_allows_microphone(self):
        """Permissions-Policy must allow microphone and camera for self so browser getUserMedia succeeds."""
        handler = object.__new__(BrowserRequestHandler)
        sent_headers = {}
        handler.send_header = lambda k, v: sent_headers.__setitem__(k, v)
        handler._security_headers()

        self.assertIn("Permissions-Policy", sent_headers)
        perm = sent_headers["Permissions-Policy"]
        self.assertIn("microphone=(self)", perm)
        self.assertIn("camera=(self)", perm)
        self.assertNotIn("microphone=()", perm)

    def test_conversational_queries_not_treated_as_automation_tasks(self):
        """Greetings and conversational turns must not be given computer automation fillers."""
        engine = FastFillerEngine()
        greetings = ["hey", "hey jarvis", "hello", "hi", "how are you", "who are you", "what can you do"]
        for g in greetings:
            self.assertTrue(engine.is_conversational(g), f"Expected '{g}' to be conversational")
            self.assertEqual(engine.get_fast_filler(g), "", f"Expected empty filler for '{g}'")

        # Computer automation tasks must still have intent and filler
        tasks = ["open notepad", "launch chrome", "search online for python docs", "delete temp folder"]
        for t in tasks:
            self.assertFalse(engine.is_conversational(t), f"Expected '{t}' not to be conversational")
            filler = engine.get_fast_filler(t)
            self.assertTrue(len(filler) > 0, f"Expected non-empty filler for task '{t}'")

    def test_supervisor_launch_task_handles_conversational_queries(self):
        """Calling launch_task on casual conversation must answer directly without automation thread."""
        agent = MagicMock()
        agent._looks_like_task.return_value = False
        agent._chat_context.return_value = "context"
        agent._maybe_chat.return_value = "Hello sir! How can I assist you today?"

        cfg = Config()
        supervisor = LiveVoiceSupervisor(cfg=cfg, agent=agent)

        with patch("jarvis.utils.voice.speak"):
            result = supervisor.launch_task("hey")

        self.assertEqual(result.get("status"), "chat")
        self.assertIn("Hello sir", result.get("reply", ""))
        self.assertFalse(supervisor.is_task_running)

    def test_browser_url_generation_with_live_voice(self):
        """When live_voice is True, the generated browser URL must contain #live=true fragment."""
        from jarvis import browser
        with patch.object(browser, "STATIC_DIR") as mock_dir, \
             patch.object(browser, "BrowserHTTPServer") as mock_server_cls, \
             patch.object(browser, "TerminalBridge") as mock_bridge_cls, \
             patch.object(browser, "_port_serves_our_token", return_value=True), \
             patch("webbrowser.open") as mock_open:

            mock_dir.__truediv__.return_value.is_file.return_value = True
            mock_server = MagicMock()
            mock_server.server_address = ("127.0.0.1", 9999)
            mock_server_cls.return_value = mock_server
            mock_bridge = MagicMock()
            mock_bridge.stopped.wait.return_value = True
            mock_bridge.process.returncode = 0
            mock_bridge.token = "testtoken"
            mock_bridge_cls.return_value = mock_bridge

            browser.run_browser(live_voice=True)

            self.assertTrue(mock_open.called)
            opened_url = mock_open.call_args[0][0]
            self.assertIn("#live=true&token=", opened_url)

    def test_live_audio_stream_suppresses_microphone_during_playback(self):
        """Microphone frames must not be forwarded to on_audio_in while speaker is playing or in echo cooldown."""
        import time
        from jarvis.live.audio import LiveAudioStream

        received_chunks = []
        stream = LiveAudioStream(
            rate_in=16000,
            rate_out=24000,
            on_audio_in=lambda b: received_chunks.append(b),
        )
        stream._is_running = True

        dummy_indata = b"\x00\x00" * 800  # 800 samples of silence

        # 1. When speaker is playing: mic audio must be dropped (echo suppression)
        stream._is_playing = True
        stream._mic_callback(dummy_indata, 800, None, None)
        self.assertEqual(len(received_chunks), 0, "Expected mic audio to be suppressed during playback")

        # 2. When playback just finished but within echo cooldown: mic audio must be dropped
        stream._is_playing = False
        stream._echo_guard_until = time.time() + 1.0  # 1s in the future
        stream._mic_callback(dummy_indata, 800, None, None)
        self.assertEqual(len(received_chunks), 0, "Expected mic audio to be suppressed during echo guard cooldown")

        # 3. When playback finished and cooldown expired: mic audio must be forwarded
        stream._echo_guard_until = time.time() - 0.1  # expired
        stream._mic_callback(dummy_indata, 800, None, None)
        self.assertEqual(len(received_chunks), 1, "Expected mic audio to be forwarded when idle")

    def test_live_state_endpoint_and_communication_agent_silencing(self):
        """When Live Voice mode is active, the Communication Agent must be completely silenced."""
        from jarvis.utils import voice
        from jarvis.browser import TerminalBridge

        # Ensure clean initial state
        voice.set_live_mode_active(False)
        self.assertFalse(voice.is_live_mode_active())

        # 1. Toggling live mode to True
        voice.set_live_mode_active(True)
        self.assertTrue(voice.is_live_mode_active())

        # 2. voice.speak must return immediately without calling TTS synthesis
        with patch.object(voice, "_speak_sync") as mock_sync, \
             patch.object(voice, "_speak_async") as mock_async:
            voice.speak("Hello, Communicating Agent should not speak!", wait=True)
            voice.speak("Speculative fast filler should not speak!", wait=False)
            mock_sync.assert_not_called()
            mock_async.assert_not_called()

        # 3. TerminalBridge must drop speech events when live_voice_active is True
        bridge = TerminalBridge(token="testtoken", live_voice=True)
        published_events = []
        bridge.broker.publish = lambda ev, **kw: published_events.append((ev, kw))

        bridge._handle_structured({
            "event": "speech",
            "active": True,
            "utterance_id": 1,
            "audio": "data:audio/wav;base64,AAAA",
        })
        self.assertEqual(len(published_events), 0, "Speech event must be suppressed when live_voice_active is True")

        # 4. Cleanup to False
        voice.set_live_mode_active(False)
        self.assertFalse(voice.is_live_mode_active())

    def test_voice_ai_system_prompt_and_tools(self):
        """Jarvis system prompt and OpenAI Realtime tools must be available for Voice AI Agent."""
        from jarvis.live.prompts import build_live_voice_system_prompt, get_live_voice_tools

        prompt = build_live_voice_system_prompt()
        self.assertIn("JARVIS", prompt)
        self.assertIn("Main Worker Agent", prompt)
        self.assertIn("execute_task", prompt)
        self.assertIn("cancel_task", prompt)

        tools = get_live_voice_tools()
        tool_names = [t.get("name") for t in tools]
        self.assertIn("execute_task", tool_names)
        self.assertIn("cancel_task", tool_names)
        self.assertIn("get_task_status", tool_names)

        # The voice agent also gets Jarvis's own actions, so it can do
        # deterministic work in one hop instead of delegating it.
        self.assertIn("run_command", tool_names)
        self.assertIn("read_file", tool_names)
        self.assertIn("open_app", tool_names)
        # ... but not the ones that need the live element list to be aimed.
        self.assertNotIn("click", tool_names)
        self.assertNotIn("type", tool_names)

        # Verify execute_task schema
        exec_tool = next(t for t in tools if t.get("name") == "execute_task")
        self.assertEqual(exec_tool["type"], "function")
        self.assertIn("task", exec_tool["parameters"]["properties"])
        self.assertIn("task", exec_tool["parameters"]["required"])

        # Verify a generated direct-tool schema carries real argument types.
        run_tool = next(t for t in tools if t.get("name") == "run_command")
        self.assertEqual(run_tool["parameters"]["properties"]["command"]["type"], "string")
        self.assertEqual(run_tool["parameters"]["required"], ["command"])

    def test_interrupt_while_idle_is_a_no_op_not_a_conflict(self):
        """A red 409 in the console every time the user stopped an idle Jarvis
        was noise, not a real failure - nothing was running to stop."""
        from http import HTTPStatus

        from jarvis.browser import BrowserRequestHandler

        handler = object.__new__(BrowserRequestHandler)
        responses: list = []
        handler._json = lambda status, body: responses.append((status, body))
        handler.server = MagicMock()
        handler.server.bridge.accepting_input = True
        handler.server.bridge.request_interrupt.return_value = (
            False, "Jarvis is ready for the next directive"
        )

        handler._handle_interrupt()

        status, body = responses[0]
        self.assertEqual(status, HTTPStatus.OK)
        self.assertTrue(body["ok"])
        self.assertFalse(body["interrupted"])

    def test_interrupt_while_busy_still_reports_success(self):
        from http import HTTPStatus

        from jarvis.browser import BrowserRequestHandler

        handler = object.__new__(BrowserRequestHandler)
        responses: list = []
        handler._json = lambda status, body: responses.append((status, body))
        handler.server = MagicMock()
        handler.server.bridge.accepting_input = False
        handler.server.bridge.request_interrupt.return_value = (True, "interrupt requested")

        handler._handle_interrupt()

        status, body = responses[0]
        self.assertEqual(status, HTTPStatus.OK)
        self.assertTrue(body["interrupted"])

    def test_interrupt_with_a_dead_runtime_is_still_a_conflict(self):
        from http import HTTPStatus

        from jarvis.browser import BrowserRequestHandler

        handler = object.__new__(BrowserRequestHandler)
        responses: list = []
        handler._json = lambda status, body: responses.append((status, body))
        handler.server = MagicMock()
        handler.server.bridge.accepting_input = False
        handler.server.bridge.request_interrupt.return_value = (
            False, "terminal runtime is not running"
        )

        handler._handle_interrupt()

        self.assertEqual(responses[0][0], HTTPStatus.CONFLICT)

    def test_live_config_never_serves_a_placeholder_ws_url(self):
        """A baked-in endpoint nobody is listening on made live mode open a
        socket to nothing, retry it forever, and still look like a session."""
        import os
        from http import HTTPStatus

        from jarvis.browser import BrowserRequestHandler
        from jarvis.config import Config

        cfg = Config()
        cfg.live_voice.ws_url = ""
        handler = object.__new__(BrowserRequestHandler)
        handler._require_api_access = lambda require_origin=False: True
        responses: list = []
        handler._json = lambda status, body: responses.append((status, body))

        with patch.dict(
            os.environ,
            {"JARVIS_LIVE_WS_URL": "", "JARVIS_REALTIME_URL": ""},
        ), patch("jarvis.config.load_config", return_value=cfg):
            handler._handle_live_config()

        self.assertEqual(responses[0][0], HTTPStatus.OK)
        self.assertEqual(responses[0][1]["ws_url"], "")

    def test_the_page_does_not_hardcode_a_voice_endpoint(self):
        """Guard the two regressions this file exists for."""
        from jarvis.browser import STATIC_DIR

        app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        # No placeholder realtime URL anywhere (it was also in the constructor).
        self.assertNotIn("SATVIKNOOB", app)
        self.assertNotIn("ws://localhost:8000", app)
        # Reconnects are bounded, and losing the endpoint stops live mode.
        self.assertIn("MAX_RECONNECT_ATTEMPTS", app)
        self.assertIn("this.reconnectAttempts >= MAX_RECONNECT_ATTEMPTS", app)
        # The public-agent fallback cannot work for a private agent on a random
        # loopback port; its only outcome was "Origin not allowed".
        self.assertNotIn("agentId: this.fishAgentId", app)

    def test_api_live_execute_delegates_task_to_main_agent(self):
        """POST /api/live/execute must submit the task to the bridge."""
        from jarvis.browser import BrowserRequestHandler
        from http import HTTPStatus

        handler = object.__new__(BrowserRequestHandler)
        handler._require_api_access = lambda require_origin=False: True
        mock_bridge = MagicMock()
        mock_bridge.submit.return_value = (True, "submitted")
        mock_bridge.state = "working"
        handler.server = MagicMock()
        handler.server.bridge = mock_bridge

        responses = []
        handler._json = lambda status, body: responses.append((status, body))

        payload = {"task": "open notepad and write hello"}
        handler.path = "/api/live/execute"

        # Simulate do_POST logic
        task = str(payload.get("task", "")).strip()
        ok, message = handler.server.bridge.submit(
            task,
            display_text=f"🎙️ [Voice Directive]: {task}",
        )
        handler._json(
            HTTPStatus.OK if ok else HTTPStatus.CONFLICT,
            {
                "ok": ok,
                "task": task,
                "message": message,
                "state": handler.server.bridge.state,
            },
        )

        mock_bridge.submit.assert_called_once_with(
            "open notepad and write hello",
            display_text="🎙️ [Voice Directive]: open notepad and write hello",
        )
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0][0], HTTPStatus.OK)
        self.assertTrue(responses[0][1]["ok"])
        self.assertEqual(responses[0][1]["task"], "open notepad and write hello")


class LiveModeFlagTests(unittest.TestCase):
    """The cross-process live-mode flag must not be able to outlive its owner.

    The flag exists because the console worker (a different process from the
    browser server) has to know when live voice owns the audio. It used to be
    trusted for as long as the file existed, so a crashed or force-killed live
    session left it behind and silenced *all* later speech - ordinary terminal
    replies included - with nothing on screen to explain the silence.
    """

    def setUp(self):
        # Never touch the user's shared live flag or physical audio devices.
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(
            patch("jarvis.utils.voice.tempfile.gettempdir", return_value=directory)
        )
        self.enterContext(patch.dict(os.environ, {"JARVIS_LIVE_MODE": "0"}))
        self.flag = pathlib.Path(directory) / "jarvis_live_mode.flag"
        from jarvis.utils import voice

        self.voice = voice
        voice._live_flag_owner_pid = None
        voice._live_flag_stop.set()
        self.addCleanup(voice.set_live_mode_active, False)

    def _backdate(self, seconds: float) -> None:
        old = time.time() - seconds
        os.utime(self.flag, (old, old))

    def test_no_flag_means_jarvis_may_speak(self):
        self.assertFalse(self.voice.is_live_mode_active())

    def test_a_fresh_flag_still_silences_jarvis(self):
        self.flag.write_text("1", encoding="utf-8")
        self.assertTrue(self.voice.is_live_mode_active())
        self.assertTrue(self.flag.exists(), "a live session's flag must survive a read")

    def test_a_flag_whose_owner_went_quiet_is_discarded(self):
        """The crash case: nothing clears the flag, so the reader must."""
        self.flag.write_text("999999", encoding="utf-8")
        self._backdate(3600)
        self.assertFalse(self.voice.is_live_mode_active())
        self.assertFalse(self.flag.exists(), "a stale flag should be cleaned up")

    def test_a_stale_flag_never_silences_ordinary_speech(self):
        """This is the symptom a user actually sees: Jarvis answers nothing."""
        self.flag.write_text("1", encoding="utf-8")
        self._backdate(self.voice._LIVE_FLAG_MAX_AGE_SECONDS + 5)
        spoken = []
        with patch.object(self.voice, "_speak_sync", side_effect=spoken.append):
            self.voice.speak("Good evening, sir.", wait=True)
        self.assertEqual(spoken, ["Good evening, sir."])

    def test_leaving_live_mode_removes_the_flag(self):
        self.voice.set_live_mode_active(True)
        self.assertTrue(self.flag.exists())
        self.voice.set_live_mode_active(False)
        self.assertFalse(self.flag.exists())
        self.assertFalse(self.voice.is_live_mode_active())

    def test_the_owner_keeps_the_flag_fresh(self):
        """A long session must never age out of its own flag."""
        with patch.object(self.voice, "_LIVE_FLAG_HEARTBEAT_SECONDS", 0.05):
            self.voice.set_live_mode_active(True)
            self.assertTrue(self.voice._live_flag_heartbeat.is_alive())
            self.flag.unlink()
            deadline = time.time() + 5
            while time.time() < deadline and not self.flag.exists():
                time.sleep(0.05)
        self.assertTrue(self.flag.exists(), "the heartbeat must restore a lost flag")

    def test_an_exit_only_clears_a_flag_this_process_raised(self):
        """Exiting must not un-mute another machine-wide live session."""
        self.flag.write_text("4242", encoding="utf-8")
        self.voice._release_live_flag_at_exit()
        self.assertTrue(self.flag.exists())

        self.voice.set_live_mode_active(True)
        self.voice._release_live_flag_at_exit()
        self.assertFalse(self.flag.exists())

    def test_an_explicit_stop_clears_another_processes_flag(self):
        """Leaving live mode ends it for the machine, whoever raised it."""
        self.flag.write_text("4242", encoding="utf-8")
        self.voice.set_live_mode_active(False)
        self.assertFalse(self.flag.exists())


if __name__ == "__main__":
    unittest.main()



