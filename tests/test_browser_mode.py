"""Browser-mode bridge, routing, and localhost security tests."""

from __future__ import annotations

import base64
from io import BytesIO
import json
from pathlib import Path
import subprocess
import struct
import signal
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import wave

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import run  # noqa: E402
from jarvis import browser_worker  # noqa: E402
from jarvis.browser import (  # noqa: E402
    BrowserHTTPServer,
    EventBroker,
    INPUT_PREFIX,
    TerminalBridge,
)
from jarvis.browser_worker import EVENT_PREFIX  # noqa: E402
from jarvis.config import Config  # noqa: E402
from jarvis.tools.apps import _is_own_console  # noqa: E402


class EventBrokerTests(unittest.TestCase):
    def test_events_are_ordered_and_replayed(self):
        broker = EventBroker(history_size=3)
        broker.publish("state", state="booting")
        broker.publish("activity", message="ready")
        subscriber, history = broker.subscribe()
        self.assertEqual([item["id"] for item in history], [1, 2])
        broker.publish("state", state="listening")
        self.assertEqual(subscriber.get(timeout=0.2)["id"], 3)
        broker.unsubscribe(subscriber)

    def test_history_is_bounded(self):
        broker = EventBroker(history_size=2)
        for number in range(4):
            broker.publish("tick", number=number)
        subscriber, history = broker.subscribe()
        self.assertEqual([item["number"] for item in history], [2, 3])
        broker.unsubscribe(subscriber)


class _FakeProcess:
    def __init__(self):
        self.stdin = BytesIO()
        self.returncode = None

    def poll(self):
        return self.returncode

    def send_signal(self, value):
        self.signal = value


class _ExitedProcess:
    def __init__(self, returncode=0):
        self.returncode = returncode

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


class TerminalBridgeInputTests(unittest.TestCase):
    def bridge(self, mode="command"):
        bridge = TerminalBridge(token="test-token")
        bridge.process = _FakeProcess()
        bridge.accepting_input = True
        bridge.input_mode = mode
        return bridge

    def test_multiline_directive_crosses_stdin_as_one_encoded_line(self):
        bridge = self.bridge()
        ok, _ = bridge.submit("first line\nsecond line")
        self.assertTrue(ok)
        wire = bridge.process.stdin.getvalue().decode("ascii").strip()
        self.assertTrue(wire.startswith(INPUT_PREFIX))
        decoded = base64.b64decode(wire[len(INPUT_PREFIX):]).decode("utf-8")
        self.assertEqual(decoded, "first line\nsecond line")
        self.assertFalse(bridge.accepting_input)

    def test_duplicate_submission_is_rejected_while_busy(self):
        bridge = self.bridge()
        self.assertTrue(bridge.submit("one")[0])
        self.assertFalse(bridge.submit("two")[0])

    def test_interrupt_signals_an_active_directive(self):
        bridge = self.bridge()
        bridge.accepting_input = False
        ok, message = bridge.request_interrupt()
        expected_signal = (
            getattr(signal, "CTRL_BREAK_EVENT", signal.SIGINT)
            if sys.platform == "win32"
            else signal.SIGINT
        )
        self.assertEqual(bridge.process.signal, expected_signal)


    def test_interrupt_rejects_when_jarvis_is_ready(self):
        bridge = self.bridge()
        self.assertFalse(bridge.request_interrupt()[0])

    def test_blank_confirmation_preserves_terminal_default_yes(self):
        bridge = self.bridge(mode="confirmation")
        self.assertTrue(bridge.submit("")[0])

    def test_blank_answer_preserves_terminal_cancel_semantics(self):
        bridge = self.bridge(mode="answer")
        self.assertTrue(bridge.submit("")[0])

    def test_blank_command_is_rejected(self):
        bridge = self.bridge()
        self.assertFalse(bridge.submit("   ")[0])

    def test_display_text_hides_wire_only_attachment_details(self):
        bridge = self.bridge()
        self.assertTrue(
            bridge.submit(
                '"C:\\Temp\\private-attachment.png" analyze this',
                display_text="Analyze this\n[Attached image: screenshot.png]",
            )[0]
        )
        subscriber, history = bridge.broker.subscribe()
        bridge.broker.unsubscribe(subscriber)
        visible = [item for item in history if item["event"] == "input"][-1]
        self.assertNotIn("private-attachment", visible["message"])
        self.assertIn("screenshot.png", visible["message"])

    def test_shutdown_cancels_nested_prompt_then_quits_main_repl(self):
        bridge = self.bridge(mode="confirmation")
        self.assertTrue(bridge.request_shutdown()[0])

        bridge._handle_structured({
            "event": "input_request",
            "mode": "command",
            "prompt": "you >",
        })
        time.sleep(0.15)

        wires = bridge.process.stdin.getvalue().decode("ascii").splitlines()
        decoded = [
            base64.b64decode(line[len(INPUT_PREFIX):]).decode("utf-8")
            for line in wires
        ]
        self.assertEqual(decoded, ["n", ":quit"])

    def test_speech_snapshot_ignores_stale_completion(self):
        bridge = TerminalBridge(token="speech-test")
        bridge._handle_structured({
            "event": "speech",
            "active": True,
            "utterance_id": 7,
            "duration_ms": 900,
            "levels": [0, 64, 255],
        })
        snapshot = bridge.speech_snapshot()
        self.assertTrue(snapshot["active"])
        self.assertEqual(snapshot["utterance_id"], 7)
        self.assertEqual(snapshot["duration_ms"], 900)
        self.assertEqual(snapshot["levels"], [0, 64, 255])

        bridge._handle_structured({
            "event": "speech",
            "active": False,
            "utterance_id": 6,
        })
        self.assertTrue(bridge.speech_snapshot()["active"])
        bridge._handle_structured({
            "event": "speech",
            "active": False,
            "utterance_id": 7,
        })
        self.assertFalse(bridge.speech_snapshot()["active"])
        bridge._handle_structured({
            "event": "speech",
            "active": True,
            "utterance_id": 7,
            "duration_ms": 1200,
            "levels": [255],
        })
        self.assertFalse(bridge.speech_snapshot()["active"])

        subscriber, history = bridge.broker.subscribe()
        bridge.broker.unsubscribe(subscriber)
        speech_events = [item for item in history if item["event"] == "speech"]
        self.assertEqual(
            [(item["active"], item["utterance_id"]) for item in speech_events],
            [(True, 7), (False, 7)],
        )

    def test_process_exit_stops_speech_and_drops_buffered_start(self):
        bridge = TerminalBridge(token="speech-exit-test")
        bridge.process = _ExitedProcess(returncode=9)
        bridge._handle_structured({
            "event": "speech",
            "active": True,
            "utterance_id": 3,
            "duration_ms": 5000,
            "levels": [32, 255],
        })

        bridge._watch_process()
        self.assertTrue(bridge.stopped.is_set())
        self.assertFalse(bridge.speech_snapshot()["active"])

        bridge._handle_structured({
            "event": "speech",
            "active": True,
            "utterance_id": 4,
            "duration_ms": 5000,
            "levels": [255],
        })
        self.assertEqual(bridge.speech_snapshot()["utterance_id"], 3)

        subscriber, history = bridge.broker.subscribe()
        bridge.broker.unsubscribe(subscriber)
        speech_events = [item for item in history if item["event"] == "speech"]
        self.assertEqual(
            [(item["active"], item["utterance_id"]) for item in speech_events],
            [(True, 3), (False, 3)],
        )
        self.assertEqual(speech_events[-1]["reason"], "session-ended")


class BrowserWorkerTests(unittest.TestCase):
    @staticmethod
    def wav_bytes(seconds=1.0, rate=1000):
        frames = max(1, round(seconds * rate))
        samples = []
        for index in range(frames):
            section = min(3, (index * 4) // frames)
            amplitude = (0, 1800, 9000, 24000)[section]
            samples.append(amplitude if index % 2 else -amplitude)
        output = BytesIO()
        with wave.open(output, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(rate)
            wav_file.writeframes(struct.pack(f"<{len(samples)}h", *samples))
        return output.getvalue()

    def setUp(self):
        browser_worker._speech_generation = 0
        browser_worker._active_speech_generation = None

    def test_wav_profile_returns_duration_and_real_bounded_envelope(self):
        duration, levels = browser_worker._wav_profile(
            self.wav_bytes(seconds=1.0),
            points=8,
        )
        self.assertAlmostEqual(duration, 1.0, places=2)
        self.assertGreaterEqual(len(levels), 4)
        self.assertLessEqual(len(levels), 8)
        self.assertTrue(all(0.0 <= level <= 1.0 for level in levels))
        self.assertAlmostEqual(max(levels), 1.0, places=3)
        self.assertGreater(len(set(levels)), 2)
        self.assertEqual(browser_worker._wav_profile(b"not a wav"), (0.0, []))

    def test_wav_spectrogram_separates_low_and_high_tones(self):
        import base64 as b64
        import math as _math

        def tone(hz, seconds=0.2, rate=8000):
            frames = round(seconds * rate)
            samples = [
                round(20000 * _math.sin(2 * _math.pi * hz * index / rate))
                for index in range(frames)
            ]
            output = BytesIO()
            with wave.open(output, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(rate)
                wav_file.writeframes(struct.pack(f"<{frames}h", *samples))
            return output.getvalue()

        def peak_band(data):
            count, fps, encoded = browser_worker._wav_spectrogram(data)
            self.assertGreater(count, 0)
            self.assertGreater(fps, 0)
            raw = b64.b64decode(encoded)
            self.assertEqual(len(raw) % count, 0)
            self.assertGreaterEqual(len(raw) // count, 2)
            middle = (len(raw) // count // 2) * count
            frame = list(raw[middle:middle + count])
            return frame.index(max(frame))

        # A 150 Hz tone must light a lower band than a 3 kHz tone, otherwise
        # every bar is showing the same number and the ring cannot move.
        self.assertLess(peak_band(tone(150)), peak_band(tone(3000)))
        self.assertEqual(browser_worker._wav_spectrogram(b"not a wav"), (0, 0, ""))

    def test_async_speech_events_use_duration_and_ignore_old_timer(self):
        timers = []
        delegated = []
        events = []

        class FakeTimer:
            def __init__(self, interval, function, args=()):
                self.interval = interval
                self.function = function
                self.args = args
                self.daemon = False
                self.started = False
                timers.append(self)

            def start(self):
                self.started = True

            def fire(self):
                self.function(*self.args)

        voice = SimpleNamespace(
            _play_wav=lambda _data, wait: delegated.append(wait),
        )
        with (
            patch.object(browser_worker, "emit",
                         side_effect=lambda event, **payload:
                         events.append((event, payload))),
            patch.object(browser_worker.threading, "Timer", FakeTimer),
        ):
            browser_worker._install_speech_bridge(voice)
            voice._play_wav(self.wav_bytes(seconds=1.0), False)
            voice._play_wav(self.wav_bytes(seconds=0.5), False)

            self.assertEqual(delegated, [False, False])
            starts = [payload for event, payload in events
                      if event == "speech" and payload["active"]]
            self.assertEqual([item["utterance_id"] for item in starts], [1, 2])
            self.assertEqual(starts[0]["duration_ms"], 1000)
            self.assertTrue(all(0 <= value <= 255 for value in starts[0]["levels"]))
            self.assertTrue(all(timer.started and timer.daemon for timer in timers))

            timers[0].fire()
            self.assertFalse(any(
                not payload["active"] for event, payload in events
                if event == "speech"
            ))
            timers[1].fire()

        stops = [payload for event, payload in events
                 if event == "speech" and not payload["active"]]
        self.assertEqual(stops, [{"active": False, "utterance_id": 2}])

    def test_sync_speech_ends_after_delegate_and_errors_still_clear(self):
        order = []
        voice = SimpleNamespace(
            _play_wav=lambda _data, _wait: order.append("delegate"),
        )
        with patch.object(
            browser_worker,
            "emit",
            side_effect=lambda _event, **payload:
            order.append("start" if payload["active"] else "stop"),
        ):
            browser_worker._install_speech_bridge(voice)
            voice._play_wav(self.wav_bytes(seconds=0.1), True)
        self.assertEqual(order, ["start", "delegate", "stop"])

        events = []
        failing_voice = SimpleNamespace(
            _play_wav=lambda *_args: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        with patch.object(
            browser_worker,
            "emit",
            side_effect=lambda event, **payload: events.append((event, payload)),
        ):
            browser_worker._install_speech_bridge(failing_voice)
            with self.assertRaisesRegex(RuntimeError, "boom"):
                failing_voice._play_wav(self.wav_bytes(seconds=0.1), False)
        self.assertEqual(
            [payload["active"] for event, payload in events if event == "speech"],
            [True, False],
        )

    def test_worker_decodes_one_wire_line_back_to_multiline_input(self):
        script = r"""
import json
from jarvis.browser_worker import install_event_bridge
install_event_bridge()
print(json.dumps({"value": input("answer > ")}, ensure_ascii=False))
"""
        value = "line one\nline two"
        wire = (
            INPUT_PREFIX
            + base64.b64encode(value.encode("utf-8")).decode("ascii")
            + "\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(Path(__file__).resolve().parent.parent),
            input=wire,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(json.dumps({"value": value}), result.stdout)
        self.assertIn(EVENT_PREFIX, result.stderr)
        self.assertIn('"event": "input_request"', result.stderr)
        self.assertIn('"mode": "answer"', result.stderr)

    def test_worker_forces_typed_repl_even_when_voice_was_persisted(self):
        cfg = Config()
        cfg.voice_enabled = True
        seen = {}
        launcher = SimpleNamespace(load_config=lambda: cfg)

        def fake_main(argv):
            seen["argv"] = argv
            seen["voice_enabled"] = launcher.load_config().voice_enabled
            return 17

        launcher.main = fake_main
        with (
            patch.object(browser_worker, "install_event_bridge"),
            patch.object(browser_worker, "emit"),
            patch.object(browser_worker, "_load_launcher", return_value=launcher),
        ):
            result = browser_worker.main(["--voice", "--vision"])

        self.assertEqual(result, 17)
        self.assertEqual(seen["argv"], ["--vision"])
        self.assertFalse(seen["voice_enabled"])

    def test_worker_and_launcher_cannot_be_shadowed_by_launch_cwd(self):
        with tempfile.TemporaryDirectory() as temp:
            cwd = Path(temp)
            sentinel = cwd / "shadow-ran.txt"
            (cwd / "run.py").write_text(
                f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('run')\n",
                encoding="utf-8",
            )
            shadow_package = cwd / "jarvis"
            shadow_package.mkdir()
            (shadow_package / "__init__.py").write_text("", encoding="utf-8")
            (shadow_package / "browser_worker.py").write_text(
                f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('worker')\n",
                encoding="utf-8",
            )

            bridge = TerminalBridge(child_args=["--help"], token="shadow-test")
            bridge.launch_cwd = cwd
            bridge.start()
            self.assertTrue(bridge.stopped.wait(10), "worker did not exit")
            bridge.stop()

            self.assertFalse(sentinel.exists())
            self.assertEqual(bridge.process.returncode, 0)


class _HTTPFakeBridge:
    def __init__(self):
        self.token = "secret-token"
        self.broker = EventBroker()
        self.alive = True
        self.state = "listening"
        self.accepting_input = True
        self.input_mode = "command"
        self.input_prompt = ""
        self.submitted = []

    def submit(self, text, display_text=None):
        self.submitted.append((text, display_text))
        return True, "submitted"

    def request_shutdown(self):
        return True, "stopping"

    def request_interrupt(self):
        return True, "interrupt requested"

    def save_attachment(self, *_args):
        raise ValueError("not used")

    def speech_snapshot(self):
        return {
            "active": False,
            "utterance_id": 0,
            "duration_ms": 0,
            "levels": [],
            "started_at": 0.0,
        }


class BrowserHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bridge = _HTTPFakeBridge()
        cls.server = BrowserHTTPServer(("127.0.0.1", 0), cls.bridge)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=1)

    def request(self, path, *, token=False, payload=None, origin=None):
        headers = {}
        if token:
            headers["X-Jarvis-Token"] = self.bridge.token
        if origin:
            headers["Origin"] = origin
        data = None
        method = "GET"
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
            method = "POST"
        request = Request(self.origin + path, data=data, headers=headers, method=method)
        return urlopen(request, timeout=2)

    def test_static_interface_is_served_without_exposing_api_token(self):
        with self.request("/") as response:
            page = response.read().decode("utf-8")
        self.assertIn("JARVIS // NEURAL INTERFACE", page)
        self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])

    def test_api_state_requires_session_token(self):
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/state")
        self.assertEqual(caught.exception.code, 401)
        with self.request("/api/state", token=True) as response:
            body = json.load(response)
        self.assertTrue(body["ok"])
        self.assertTrue(body["accepting_input"])
        self.assertFalse(body["speech"]["active"])

    def test_static_assets_include_speech_spectrum_overlay(self):
        with self.request("/app.js") as response:
            app = response.read().decode("utf-8")
        with self.request("/styles.css") as response:
            styles = response.read().decode("utf-8")
        self.assertIn('case "speech"', app)
        self.assertIn("drawVoiceSpectrum", app)
        self.assertIn("setSpeaking", app)
        self.assertIn("speechExpiryTimer", app)
        self.assertIn("speechIdWatermark", app)
        self.assertIn('data-speaking="true"', styles)

    def test_static_interface_replaces_send_with_stop_while_generating(self):
        with self.request("/") as response:
            page = response.read().decode("utf-8")
        with self.request("/app.js") as response:
            app = response.read().decode("utf-8")
        with self.request("/styles.css") as response:
            styles = response.read().decode("utf-8")
        self.assertIn('id="sendButtonLabel"', page)
        self.assertIn('elements.sendLabel.textContent = canStop ? "STOP" : "SEND"', app)
        self.assertIn('await api("/api/interrupt", {})', app)
        self.assertIn('.send-button.is-stop', styles)

    def test_non_ascii_token_is_rejected_without_handler_error(self):
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/state?token=%C3%A9")
        self.assertEqual(caught.exception.code, 401)

    def test_input_requires_exact_origin_and_token(self):
        with self.assertRaises(HTTPError) as caught:
            self.request(
                "/api/input",
                token=True,
                payload={"text": "hello"},
                origin="https://malicious.example",
            )
        self.assertEqual(caught.exception.code, 403)

        with self.request(
            "/api/input",
            token=True,
            payload={"text": "hello"},
            origin=self.origin,
        ) as response:
            body = json.load(response)
        self.assertTrue(body["ok"])
        self.assertEqual(self.bridge.submitted[-1], ("hello", None))

    def test_non_ascii_origin_is_rejected_without_handler_error(self):
        with self.assertRaises(HTTPError) as caught:
            self.request(
                "/api/input",
                token=True,
                payload={"text": "hello"},
                origin="http://é.example",
            )
        self.assertEqual(caught.exception.code, 403)

    def test_input_accepts_separate_safe_display_text(self):
        with self.request(
            "/api/input",
            token=True,
            payload={
                "text": '"C:\\Temp\\private.png" inspect',
                "display_text": "Inspect\n[Attached image: capture.png]",
            },
            origin=self.origin,
        ) as response:
            body = json.load(response)
        self.assertTrue(body["ok"])
        self.assertEqual(
            self.bridge.submitted[-1],
            (
                '"C:\\Temp\\private.png" inspect',
                "Inspect\n[Attached image: capture.png]",
            ),
        )

    def test_interrupt_requires_exact_origin_and_token(self):
        with self.assertRaises(HTTPError) as caught:
            self.request(
                "/api/interrupt",
                token=True,
                payload={},
                origin="https://malicious.example",
            )
        self.assertEqual(caught.exception.code, 403)

        with self.request(
            "/api/interrupt",
            token=True,
            payload={},
            origin=self.origin,
        ) as response:
            body = json.load(response)
        self.assertTrue(body["ok"])

    def test_head_event_stream_is_rejected_without_blocking(self):
        request = Request(
            self.origin + "/api/events",
            headers={"X-Jarvis-Token": self.bridge.token},
            method="HEAD",
        )
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 405)

    def test_arbitrary_static_paths_are_not_served(self):
        with self.assertRaises(HTTPError) as caught:
            self.request("/../config.yaml")
        self.assertEqual(caught.exception.code, 404)


class CLIRoutingTests(unittest.TestCase):
    def test_browser_flag_forwards_overrides_and_initial_task(self):
        cfg = Config()
        with (
            patch.object(run, "load_config", return_value=cfg),
            patch("jarvis.browser.run_browser", return_value=23) as browser,
        ):
            result = run.main([
                "--browser",
                "--backend", "ollama",
                "--model", "demo:latest",
                "--vision",
                "--voice",
                "--confirm",
                "--steps", "9",
                "open", "notepad",
            ])
        self.assertEqual(result, 23)
        browser.assert_called_once_with(
            child_args=[
                "--backend", "ollama",
                "--model", "demo:latest",
                "--vision",
                "--confirm",
                "--steps", "9",
            ],
            initial_task="open notepad",
        )

    def test_check_keeps_precedence_over_browser(self):
        cfg = Config()
        with (
            patch.object(run, "load_config", return_value=cfg),
            patch.object(run, "run_check", return_value=7) as check,
            patch("jarvis.browser.run_browser") as browser,
        ):
            result = run.main(["--browser", "--check"])
        self.assertEqual(result, 7)
        check.assert_called_once_with(cfg)
        browser.assert_not_called()


class OwnInterfaceProtectionTests(unittest.TestCase):
    def test_browser_page_is_protected_like_the_terminal(self):
        window = type(
            "Window",
            (),
            {"title": "JARVIS // NEURAL INTERFACE - Google Chrome", "_hWnd": 99},
        )()
        self.assertTrue(_is_own_console(window, own=(0, "")))


if __name__ == "__main__":
    unittest.main()
