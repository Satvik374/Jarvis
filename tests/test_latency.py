from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from jarvis.agent import loop
from jarvis.agent.brain import (
    AnthropicBrain,
    Brain,
    GeminiVertexBrain,
    OllamaBrain,
    OpenAICompatBrain,
)
from jarvis.config import BrainConfig, VoiceConfig
from jarvis.utils import logging as log
from jarvis.utils import voice


class HTTPConnectionReuseTests(unittest.TestCase):
    def test_same_thread_reuses_one_session(self):
        brain = Brain(BrainConfig())
        session = Mock()
        with patch("requests.Session", return_value=session) as factory:
            brain._http_post("https://example.test/one", json={"one": 1})
            brain._http_post("https://example.test/two", json={"two": 2})

        factory.assert_called_once_with()
        self.assertEqual(session.post.call_count, 2)
        self.assertEqual(
            session.post.call_args_list[1].args[0],
            "https://example.test/two",
        )

    def test_concurrent_threads_get_independent_sessions(self):
        brain = Brain(BrainConfig())
        created = []
        created_lock = threading.Lock()

        def make_session():
            session = Mock()
            with created_lock:
                created.append(session)
            return session

        barrier = threading.Barrier(3)

        def request():
            barrier.wait()
            brain._http_post("https://example.test/model")

        with patch("requests.Session", side_effect=make_session):
            threads = [threading.Thread(target=request) for _ in range(2)]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join(timeout=2)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(created), 2)
        self.assertTrue(all(session.post.call_count == 1 for session in created))

    def test_concurrent_warmup_refreshes_vertex_credentials_once(self):
        brain = GeminiVertexBrain(BrainConfig())
        refresh_started = threading.Event()
        release_refresh = threading.Event()
        refresh_count = 0
        count_lock = threading.Lock()

        def refresh():
            nonlocal refresh_count
            with count_lock:
                refresh_count += 1
            refresh_started.set()
            release_refresh.wait(2)
            brain._cached_token = "token"
            brain.project_id = "project"
            brain._token_expiry = time.time() + 3600
            return "token", "project"

        brain._refresh_access_token_and_project = refresh
        results = []
        threads = [
            threading.Thread(
                target=lambda: results.append(
                    brain._get_access_token_and_project()
                )
            )
            for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        self.assertTrue(refresh_started.wait(1))
        release_refresh.set()
        for thread in threads:
            thread.join(timeout=2)

        self.assertEqual(refresh_count, 1)
        self.assertEqual(results, [("token", "project")] * 2)

    def test_partial_vertex_cache_never_reaches_foreground_request(self):
        brain = GeminiVertexBrain(BrainConfig())
        brain._cached_token = "warmup-token"
        brain._token_expiry = time.time() + 3600
        brain.project_id = None
        brain._refresh_access_token_and_project = Mock(
            return_value=("fresh-token", "real-project"),
        )

        result = brain._get_access_token_and_project()

        self.assertEqual(result, ("fresh-token", "real-project"))
        brain._refresh_access_token_and_project.assert_called_once_with()


class BackendTransportRegressionTests(unittest.TestCase):
    def test_ollama_response_and_request_shape_are_unchanged(self):
        cfg = BrainConfig(
            backend="ollama",
            model="test-model",
            base_url="http://ollama.test",
        )
        brain = OllamaBrain(cfg)
        response = Mock(status_code=200)
        response.json.return_value = {
            "message": {"content": '{"action":"finish"}'},
        }
        brain._http_post = Mock(return_value=response)

        result = brain.complete("system", [{"role": "user", "content": "task"}])

        self.assertEqual(result, '{"action":"finish"}')
        call = brain._http_post.call_args
        self.assertEqual(call.args[0], "http://ollama.test/api/chat")
        self.assertEqual(call.kwargs["json"]["model"], "test-model")
        self.assertFalse(call.kwargs["json"]["stream"])
        self.assertEqual(call.kwargs["json"]["format"], "json")

    def test_openai_compatible_response_and_request_shape_are_unchanged(self):
        cfg = BrainConfig(
            backend="openai",
            model="test-model",
            base_url="https://openai.test/v1",
            api_key="secret",
        )
        brain = OpenAICompatBrain(cfg)
        response = Mock()
        response.json.return_value = {
            "choices": [{"message": {"content": "answer"}}],
        }
        brain._http_post = Mock(return_value=response)

        result = brain.complete("system", [{"role": "user", "content": "task"}])

        self.assertEqual(result, "answer")
        call = brain._http_post.call_args
        self.assertEqual(call.args[0], "https://openai.test/v1/chat/completions")
        self.assertEqual(call.kwargs["json"]["model"], "test-model")
        self.assertEqual(
            call.kwargs["headers"]["Authorization"],
            "Bearer secret",
        )

    def test_anthropic_response_and_request_shape_are_unchanged(self):
        cfg = BrainConfig(
            backend="anthropic",
            model="test-model",
            api_key="secret",
        )
        brain = AnthropicBrain(cfg)
        response = Mock()
        response.json.return_value = {
            "content": [
                {"type": "text", "text": "one"},
                {"type": "text", "text": " two"},
            ],
        }
        brain._http_post = Mock(return_value=response)

        result = brain.complete("system", [{"role": "user", "content": "task"}])

        self.assertEqual(result, "one two")
        call = brain._http_post.call_args
        self.assertEqual(
            call.args[0],
            "https://api.anthropic.com/v1/messages",
        )
        self.assertEqual(call.kwargs["json"]["model"], "test-model")
        self.assertEqual(call.kwargs["headers"]["x-api-key"], "secret")


class ScreenshotArchivalTests(unittest.TestCase):
    def test_diagnostic_png_is_queued_while_raw_image_returns_immediately(self):
        agent = loop.Agent.__new__(loop.Agent)
        agent.cfg = SimpleNamespace(
            brain=SimpleNamespace(use_vision=True),
            perception=SimpleNamespace(save_screenshots=True),
        )
        agent._shot_dir = Path(tempfile.gettempdir()) / "jarvis-latency-test"

        raw_image = Mock(name="raw-image")
        shot = loop.screen_mod.Screenshot(
            image=raw_image,
            width=1920,
            height=1080,
        )
        observation = SimpleNamespace(screenshot_path=None)

        with (
            patch.object(loop.screen_mod, "capture", return_value=shot),
            patch.object(
                loop.screen_mod,
                "timestamped_name",
                return_value="step.png",
            ),
            patch.object(loop._SCREENSHOT_ARCHIVER, "submit") as submit,
        ):
            result = agent._maybe_image(observation, 1)

        self.assertIs(result, raw_image)
        submit.assert_called_once()
        queued_obs, queued_shot, path = submit.call_args.args
        self.assertIs(queued_obs, observation)
        self.assertIs(queued_shot, shot)
        self.assertEqual(path.name, "step.png")

    def test_archiver_bounds_pending_frames_before_copying_images(self):
        archiver = loop._ScreenshotArchiver(capacity=2)
        image = Mock(name="live-image")
        image.copy.side_effect = [
            Mock(name="archive-image-1"),
            Mock(name="archive-image-2"),
        ]
        shot = loop.screen_mod.Screenshot(
            image=image,
            width=1920,
            height=1080,
        )
        observations = [
            SimpleNamespace(screenshot_path=None)
            for _ in range(3)
        ]
        first_started = threading.Event()
        release_first = threading.Event()
        second_finished = threading.Event()

        def archive(obs, _shot, _path):
            if obs is observations[0]:
                first_started.set()
                release_first.wait(2)
            elif obs is observations[1]:
                second_finished.set()

        try:
            with patch.object(
                loop,
                "_archive_screenshot",
                side_effect=archive,
            ):
                self.assertTrue(
                    archiver.submit(observations[0], shot, Path("one.png"))
                )
                self.assertTrue(first_started.wait(1))
                self.assertTrue(
                    archiver.submit(observations[1], shot, Path("two.png"))
                )
                self.assertFalse(
                    archiver.submit(observations[2], shot, Path("three.png"))
                )
                self.assertEqual(image.copy.call_count, 2)
                release_first.set()
                self.assertTrue(second_finished.wait(1))
        finally:
            release_first.set()


class AsyncSpeechTests(unittest.TestCase):
    def tearDown(self):
        voice.reset()

    def test_non_waiting_speech_returns_before_cloud_synthesis_finishes(self):
        voice.configure(Mock(), VoiceConfig())
        synthesis_started = threading.Event()
        release_synthesis = threading.Event()
        playback_finished = threading.Event()

        def synthesize(_text, *_args, **_kwargs):
            synthesis_started.set()
            release_synthesis.wait(2)
            return b"wav"

        def play(_data, wait):
            self.assertFalse(wait)
            playback_finished.set()

        with (
            patch.object(voice, "_synthesize_wav", side_effect=synthesize),
            patch.object(voice, "_play_wav", side_effect=play),
        ):
            started = time.perf_counter()
            voice.speak("Hello", wait=False)
            elapsed = time.perf_counter() - started
            self.assertLess(elapsed, 0.1)
            self.assertTrue(synthesis_started.wait(1))
            self.assertFalse(playback_finished.is_set())
            release_synthesis.set()
            self.assertTrue(playback_finished.wait(1))

    def test_superseded_synthesis_cannot_interrupt_newer_speech(self):
        voice.configure(Mock(), VoiceConfig())
        first_started = threading.Event()
        release_first = threading.Event()
        second_started = threading.Event()
        second_played = threading.Event()
        played = []

        def synthesize(text, *_args, **_kwargs):
            if text == "first":
                first_started.set()
                release_first.wait(2)
            elif text == "second":
                second_started.set()
            return text.encode("ascii")

        def play(data, _wait):
            played.append(data)
            if data == b"second":
                second_played.set()

        with (
            patch.object(voice, "_synthesize_wav", side_effect=synthesize),
            patch.object(voice, "_play_wav", side_effect=play),
        ):
            voice.speak("first")
            self.assertTrue(first_started.wait(1))
            voice.speak("second")
            # The new reply must begin before the obsolete cloud request
            # returns; otherwise one slow request serializes all later speech.
            self.assertTrue(second_started.wait(1))
            self.assertTrue(second_played.wait(1))
            release_first.set()

        self.assertEqual(played, [b"second"])

    def test_waiting_speech_bypasses_a_stalled_async_request(self):
        voice.configure(Mock(), VoiceConfig())
        async_started = threading.Event()
        release_async = threading.Event()
        played = []

        def synthesize(text, *_args, **_kwargs):
            if text == "old":
                async_started.set()
                release_async.wait(2)
            return text.encode("ascii")

        with (
            patch.object(voice, "_synthesize_wav", side_effect=synthesize),
            patch.object(
                voice,
                "_play_wav",
                side_effect=lambda data, wait: played.append((data, wait)),
            ),
        ):
            voice.speak("old", wait=False)
            self.assertTrue(async_started.wait(1))
            voice.speak("Goodbye", wait=True)
            self.assertEqual(played, [(b"Goodbye", True)])
            release_async.set()

        self.assertEqual(played, [(b"Goodbye", True)])

    def test_newer_async_reply_does_not_cancel_waiting_speech(self):
        voice.configure(Mock(), VoiceConfig())
        sync_started = threading.Event()
        release_sync = threading.Event()
        newer_synthesized = threading.Event()
        newer_played = threading.Event()
        played = []

        def synthesize(text, *_args, **_kwargs):
            if text == "Goodbye":
                sync_started.set()
                release_sync.wait(2)
            elif text == "newer":
                newer_synthesized.set()
            return text.encode("ascii")

        def play(data, wait):
            played.append((data, wait))
            if data == b"newer":
                newer_played.set()

        with (
            patch.object(voice, "_synthesize_wav", side_effect=synthesize),
            patch.object(voice, "_play_wav", side_effect=play),
        ):
            sync_thread = threading.Thread(
                target=voice.speak,
                args=("Goodbye",),
                kwargs={"wait": True},
            )
            sync_thread.start()
            self.assertTrue(sync_started.wait(1))
            voice.speak("newer", wait=False)
            self.assertTrue(newer_synthesized.wait(1))
            release_sync.set()
            sync_thread.join(timeout=1)
            self.assertFalse(sync_thread.is_alive())
            self.assertTrue(newer_played.wait(1))

        self.assertEqual(
            played,
            [(b"Goodbye", True), (b"newer", False)],
        )

    def test_async_dispatcher_has_fixed_concurrency_and_coalesces_backlog(self):
        voice.configure(Mock(), VoiceConfig())
        release = threading.Event()
        first_started = threading.Event()
        two_active = threading.Event()
        latest_played = threading.Event()
        active = 0
        max_active = 0
        active_lock = threading.Lock()
        played = []

        def synthesize(text, *_args, **_kwargs):
            nonlocal active, max_active
            if text == "first":
                first_started.set()
            with active_lock:
                active += 1
                max_active = max(max_active, active)
                if active == 2:
                    two_active.set()
            release.wait(2)
            with active_lock:
                active -= 1
            return text.encode("ascii")

        def play(data, _wait):
            played.append(data)
            if data == b"latest":
                latest_played.set()

        with (
            patch.object(voice, "_synthesize_wav", side_effect=synthesize),
            patch.object(voice, "_play_wav", side_effect=play),
        ):
            voice.speak("first")
            self.assertTrue(first_started.wait(1))
            voice.speak("second")
            self.assertTrue(two_active.wait(1))
            for text in ("third", "fourth", "fifth", "latest"):
                voice.speak(text)
            self.assertEqual(max_active, 2)
            self.assertEqual(len(voice._SPEECH_DISPATCHER._workers), 2)
            self.assertTrue(all(
                worker.daemon
                for worker in voice._SPEECH_DISPATCHER._workers
            ))
            release.set()
            self.assertTrue(latest_played.wait(1))

        self.assertLessEqual(max_active, 2)
        self.assertEqual(played, [b"latest"])

    def test_reset_does_not_wait_for_synchronous_cloud_synthesis(self):
        voice.configure(Mock(), VoiceConfig())
        synthesis_started = threading.Event()
        release_synthesis = threading.Event()
        played = []

        def synthesize(_text, *_args, **_kwargs):
            synthesis_started.set()
            release_synthesis.wait(2)
            return b"wav"

        with (
            patch.object(voice, "_synthesize_wav", side_effect=synthesize),
            patch.object(
                voice,
                "_play_wav",
                side_effect=lambda data, wait: played.append((data, wait)),
            ),
        ):
            sync_thread = threading.Thread(
                target=voice.speak,
                args=("Goodbye",),
                kwargs={"wait": True},
            )
            sync_thread.start()
            self.assertTrue(synthesis_started.wait(1))
            started = time.perf_counter()
            voice.reset()
            self.assertLess(time.perf_counter() - started, 0.1)
            release_synthesis.set()
            sync_thread.join(timeout=1)
            self.assertFalse(sync_thread.is_alive())

        self.assertEqual(played, [])

    def test_reset_cannot_return_before_sync_playback_handoff_finishes(self):
        voice.configure(Mock(), VoiceConfig())
        playback_started = threading.Event()
        release_playback = threading.Event()
        reset_finished = threading.Event()
        self.addCleanup(release_playback.set)

        def play(_data, wait):
            self.assertTrue(wait)
            playback_started.set()
            release_playback.wait(2)

        with (
            patch.object(
                voice,
                "_synthesize_wav",
                return_value=b"wav",
            ),
            patch.object(voice, "_play_wav", side_effect=play),
        ):
            sync_thread = threading.Thread(
                target=voice.speak,
                args=("Goodbye",),
                kwargs={"wait": True},
            )
            sync_thread.start()
            self.assertTrue(playback_started.wait(1))
            reset_thread = threading.Thread(
                target=lambda: (
                    voice.reset(),
                    reset_finished.set(),
                ),
            )
            reset_thread.start()
            self.assertFalse(reset_finished.wait(0.05))
            release_playback.set()
            sync_thread.join(timeout=1)
            reset_thread.join(timeout=1)

        self.assertFalse(sync_thread.is_alive())
        self.assertFalse(reset_thread.is_alive())
        self.assertTrue(reset_finished.is_set())

    def test_async_submission_does_not_wait_for_sync_playback(self):
        voice.configure(Mock(), VoiceConfig())
        playback_started = threading.Event()
        release_playback = threading.Event()
        submitted = threading.Event()
        submission_returned = threading.Event()
        self.addCleanup(release_playback.set)

        def play(_data, wait):
            self.assertTrue(wait)
            playback_started.set()
            release_playback.wait(2)

        with (
            patch.object(
                voice,
                "_synthesize_wav",
                return_value=b"wav",
            ),
            patch.object(voice, "_play_wav", side_effect=play),
            patch.object(
                voice._SPEECH_DISPATCHER,
                "submit",
                side_effect=lambda *_args: submitted.set(),
            ),
        ):
            sync_thread = threading.Thread(
                target=voice.speak,
                args=("Goodbye",),
                kwargs={"wait": True},
            )
            sync_thread.start()
            self.assertTrue(playback_started.wait(1))
            submit_thread = threading.Thread(
                target=lambda: (
                    voice.speak("new reply", wait=False),
                    submission_returned.set(),
                ),
            )
            submit_thread.start()
            self.assertTrue(submission_returned.wait(1))
            self.assertTrue(submitted.is_set())
            release_playback.set()
            sync_thread.join(timeout=1)
            submit_thread.join(timeout=1)

        self.assertFalse(sync_thread.is_alive())
        self.assertFalse(submit_thread.is_alive())

    def test_reconfigure_cleanup_cannot_discard_a_newer_submission(self):
        cleanup_started = threading.Event()
        release_cleanup = threading.Event()
        submitted = threading.Event()
        self.addCleanup(release_cleanup.set)

        def discard_pending():
            cleanup_started.set()
            release_cleanup.wait(2)

        with (
            patch.object(
                voice._SPEECH_DISPATCHER,
                "discard_pending",
                side_effect=discard_pending,
            ),
            patch.object(
                voice._SPEECH_DISPATCHER,
                "submit",
                side_effect=lambda *_args: submitted.set(),
            ),
            patch.object(voice, "_stop_async_playback"),
        ):
            configure_thread = threading.Thread(
                target=voice.configure,
                args=(Mock(), VoiceConfig()),
            )
            configure_thread.start()
            self.assertTrue(cleanup_started.wait(1))
            speak_thread = threading.Thread(
                target=voice.speak,
                args=("new reply",),
            )
            speak_thread.start()
            self.assertFalse(submitted.wait(0.05))
            release_cleanup.set()
            configure_thread.join(timeout=1)
            speak_thread.join(timeout=1)

        self.assertFalse(configure_thread.is_alive())
        self.assertFalse(speak_thread.is_alive())
        self.assertTrue(submitted.is_set())

    def test_waiting_speech_remains_synchronous(self):
        voice.configure(Mock(), VoiceConfig())
        order = []
        with (
            patch.object(
                voice,
                "_synthesize_wav",
                side_effect=lambda _text, *_args, **_kwargs:
                order.append("synth") or b"wav",
            ),
            patch.object(
                voice,
                "_play_wav",
                side_effect=lambda _data, wait:
                order.append("play-wait" if wait else "play-async"),
            ),
        ):
            voice.speak("Goodbye", wait=True)
            order.append("returned")

        self.assertEqual(order, ["synth", "play-wait", "returned"])


class CompletionCueTests(unittest.TestCase):
    def test_completion_cue_does_not_delay_the_result(self):
        beep_started = threading.Event()
        release_beep = threading.Event()
        cue_finished = threading.Event()
        calls = []

        def beep(frequency, duration):
            calls.append((frequency, duration))
            if len(calls) == 1:
                beep_started.set()
                release_beep.wait(2)
            if len(calls) == 2:
                cue_finished.set()

        fake_winsound = SimpleNamespace(Beep=beep)
        with (
            patch.object(log.sys, "platform", "win32"),
            patch.dict(sys.modules, {"winsound": fake_winsound}),
        ):
            started = time.perf_counter()
            log.pop(success=True)
            elapsed = time.perf_counter() - started
            self.assertLess(elapsed, 0.1)
            self.assertTrue(beep_started.wait(1))
            release_beep.set()
            self.assertTrue(cue_finished.wait(1))

        self.assertEqual(calls, [(880, 90), (1320, 130)])


if __name__ == "__main__":
    unittest.main()
