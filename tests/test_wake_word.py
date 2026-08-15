"""Hands-free wake-word ("Hey Jarvis") wiring: CLI flag, config, console loop,
and jarvis.utils.voice.wait_for_wake's own bounded-wait/robustness logic."""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import run  # noqa: E402
from jarvis import console  # noqa: E402
from jarvis.config import Config, load_config  # noqa: E402


class WakeCLIFlagTests(unittest.TestCase):
    def test_wake_flag_sets_config_and_enters_repl(self):
        cfg = Config()
        with (
            patch.object(run, "load_config", return_value=cfg),
            patch("jarvis.console.repl", return_value=0) as repl,
        ):
            result = run.main(["--wake"])
        self.assertEqual(result, 0)
        self.assertTrue(cfg.wake_enabled)
        repl.assert_called_once_with(cfg)

    def test_wake_flag_env_override(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text("brain:\n  backend: gemini\n", encoding="utf-8")
            with patch("dotenv.load_dotenv", lambda *a, **k: None), \
                    patch.dict(os.environ):
                os.environ.pop("JARVIS_WAKE", None)
                cfg = load_config(config_path)
                self.assertFalse(cfg.wake_enabled)
                os.environ["JARVIS_WAKE"] = "true"
                cfg = load_config(config_path)
                self.assertTrue(cfg.wake_enabled)


class WakeLoopTests(unittest.TestCase):
    def _agent(self, result="done"):
        agent = Mock()
        agent.run.return_value = result
        return agent

    def test_wake_loop_asks_what_to_do_then_runs_and_reports_then_rewaits(self):
        """One full "Hey Jarvis" -> ask -> task -> summary -> rewait cycle."""
        agent = self._agent("Opened Notepad and typed the note.")
        spoken: list[str] = []

        wake_calls = {"n": 0}

        def fake_wait_for_wake(*a, **k):
            wake_calls["n"] += 1
            if wake_calls["n"] == 1:
                return True
            raise KeyboardInterrupt   # stop the loop after one full cycle

        with (
            patch.object(console.voice, "speak",
                        side_effect=lambda text, **k: spoken.append(text)),
            patch.object(console.voice, "wait_for_wake", side_effect=fake_wait_for_wake),
            patch.object(console.voice, "listen", return_value=b"fake-wav"),
            patch.object(console.voice, "transcribe", return_value="open notepad"),
            patch.object(console.scheduler, "desktop") as desktop_cm,
        ):
            desktop_cm.return_value.__enter__ = Mock(return_value=None)
            desktop_cm.return_value.__exit__ = Mock(return_value=False)
            with self.assertRaises(KeyboardInterrupt):
                console._wake_loop(agent, Config())

        # It asked what to do after waking, ran the heard task, and spoke
        # back a summary - in that order.
        self.assertIn("Yes? What would you like me to do?", spoken)
        agent.run.assert_called_once()
        self.assertEqual(agent.run.call_args.args[0], "open notepad")
        self.assertIn("Opened Notepad and typed the note.", spoken)
        ask_index = spoken.index("Yes? What would you like me to do?")
        summary_index = spoken.index("Opened Notepad and typed the note.")
        self.assertLess(ask_index, summary_index)

    def test_wake_loop_exits_quietly_when_listener_unavailable(self):
        agent = self._agent()
        with (
            patch.object(console.voice, "speak"),
            patch.object(console.voice, "wait_for_wake", return_value=False),
        ):
            console._wake_loop(agent, Config())   # returns, doesn't raise
        agent.run.assert_not_called()

    def test_announce_false_skips_the_startup_greeting(self):
        agent = self._agent()
        with (
            patch.object(console.voice, "speak") as speak,
            patch.object(console.voice, "wait_for_wake", return_value=False),
        ):
            console._wake_loop(agent, Config(), announce=False)
        speak.assert_not_called()


class WaitForWakeRobustnessTests(unittest.TestCase):
    """Exercise jarvis.utils.voice.wait_for_wake's own bounded-wait logic
    directly (mocking only the worker plumbing), without going through the
    console loop or touching real SAPI/COM."""

    def setUp(self):
        from jarvis.utils import voice
        self.voice = voice
        # Each test gets a clean slate: no leftover worker thread from a
        # previous test or a previous real (non-mocked) run in this process.
        with voice._wake_worker_lock:
            voice._wake_worker = None

    def tearDown(self):
        with self.voice._wake_worker_lock:
            self.voice._wake_worker = None

    def test_returns_false_promptly_when_engine_never_responds(self):
        """If the worker thread never reports 'ready' (a wedged SAPI call),
        wait_for_wake must give up on its own bounded grace period instead
        of hanging the caller forever."""
        voice = self.voice

        def fake_ensure_worker():
            requests = voice.queue.Queue()  # nothing ever drains this
            thread = type("FakeThread", (), {"is_alive": lambda self: True})()
            return {"thread": thread, "requests": requests}

        with (
            patch.object(voice, "_WAKE_STARTUP_GRACE", 0.05),
            patch.object(voice, "_ensure_wake_worker", side_effect=fake_ensure_worker),
        ):
            t0 = time.time()
            result = voice.wait_for_wake("hey jarvis", timeout=5.0)
            elapsed = time.time() - t0
        self.assertFalse(result)
        self.assertLess(elapsed, 1.0)   # bounded by the grace period, not `timeout`

    def test_returns_true_when_worker_reports_heard(self):
        """A healthy worker that reports ready then heard=True round-trips
        cleanly back through wait_for_wake."""
        voice = self.voice

        def fake_ensure_worker():
            requests = voice.queue.Queue()

            def _serve():
                phrase, timeout, ready_q, done_q, cancel_event = requests.get()
                ready_q.put(("ok", None))
                done_q.put(("ok", True))

            thread = type("FakeThread", (), {"is_alive": lambda self: True})()
            threading.Thread(target=_serve, daemon=True).start()
            return {"thread": thread, "requests": requests}

        with patch.object(voice, "_ensure_wake_worker", side_effect=fake_ensure_worker):
            result = voice.wait_for_wake("hey jarvis", timeout=5.0)
        self.assertTrue(result)

    def test_worker_error_is_reported_and_returns_false(self):
        voice = self.voice

        def fake_ensure_worker():
            requests = voice.queue.Queue()

            def _serve():
                phrase, timeout, ready_q, done_q, cancel_event = requests.get()
                ready_q.put(("error", RuntimeError("boom")))

            thread = type("FakeThread", (), {"is_alive": lambda self: True})()
            threading.Thread(target=_serve, daemon=True).start()
            return {"thread": thread, "requests": requests}

        with patch.object(voice, "_ensure_wake_worker", side_effect=fake_ensure_worker):
            result = voice.wait_for_wake("hey jarvis", timeout=5.0)
        self.assertFalse(result)

    def test_keyboard_interrupt_sets_cancel_event_and_propagates(self):
        """Ctrl+C while waiting for the phrase must still reach the caller
        AND signal the worker to stop, so a later :wake doesn't queue behind
        an abandoned, still-running wait."""
        voice = self.voice
        captured: dict = {}

        class _InterruptingDoneQueue:
            """Stands in for done_q: get() raises KeyboardInterrupt, as if
            the user hit Ctrl+C while wait_for_wake was blocked on it."""

            def put(self, item):
                pass

            def get(self, timeout=None):
                raise KeyboardInterrupt()

        class _OneShotBox:
            """Single-item handoff that doesn't touch queue.Queue at all, so
            patching that class below can't accidentally intercept it too."""

            def __init__(self):
                self._event = threading.Event()
                self._value = None

            def put(self, value):
                self._value = value
                self._event.set()

            def get(self):
                self._event.wait()
                return self._value

        def fake_ensure_worker():
            requests = _OneShotBox()

            def _serve():
                phrase, timeout, ready_q, done_q, cancel_event = requests.get()
                captured["cancel_event"] = cancel_event
                ready_q.put(("ok", None))

            thread = type("FakeThread", (), {"is_alive": lambda self: True})()
            threading.Thread(target=_serve, daemon=True).start()
            return {"thread": thread, "requests": requests}

        real_queue_cls = voice.queue.Queue

        with (
            patch.object(voice, "_ensure_wake_worker", side_effect=fake_ensure_worker),
            patch.object(voice.queue, "Queue", side_effect=[
                real_queue_cls(),             # ready_q: real, used normally
                _InterruptingDoneQueue(),     # done_q: raises on get()
            ]),
        ):
            with self.assertRaises(KeyboardInterrupt):
                voice.wait_for_wake("hey jarvis", timeout=None)

        # Give the worker thread a moment to receive the request and store
        # the cancel_event before asserting on it.
        for _ in range(50):
            if "cancel_event" in captured:
                break
            time.sleep(0.02)
        self.assertIn("cancel_event", captured)
        self.assertTrue(captured["cancel_event"].is_set())


if __name__ == "__main__":
    unittest.main()
