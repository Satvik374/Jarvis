"""Comprehensive test suite for Gemini 3.1 Flash Live Voice integration and Supervisor."""

import base64
import json
import os
import queue
import threading
import time
import unittest
from unittest.mock import MagicMock, Mock, patch

from jarvis.config import Config, LiveVoiceConfig, load_config
from jarvis.agent.loop import Agent
from jarvis.agent.brain import Brain
from jarvis.live.audio import LiveAudioStream
from jarvis.live.client import GeminiLiveClient
from jarvis.live.supervisor import LiveVoiceSupervisor


class DummyBrain(Brain):
    """Mock brain for fast unit tests."""

    def __init__(self, cfg=None):
        if cfg is None:
            c = Config()
            cfg = c.brain
        super().__init__(cfg)
        self.responses = []
        self.call_count = 0

    def complete(self, system: str, messages: list[dict], image=None) -> str:
        self.call_count += 1
        if self.responses:
            return self.responses.pop(0)
        return json.dumps({
            "thought": "All done with test.",
            "action": "finish",
            "args": {"message": "Test task completed successfully."},
        })


class LiveVoiceConfigTests(unittest.TestCase):
    """Test LiveVoiceConfig initialization and loading."""

    def test_01_default_live_voice_config(self):
        cfg = Config()
        self.assertIsNotNone(cfg.live_voice)
        self.assertEqual(cfg.live_voice.model, "gemini-3.1-flash-live-preview")
        self.assertEqual(cfg.live_voice.voice_name, "Aoede")
        self.assertEqual(cfg.live_voice.location, "us-central1")
        self.assertEqual(cfg.live_voice.backend, "api_key")
        self.assertTrue(cfg.live_voice.narrate_steps)
        self.assertEqual(cfg.live_voice.sample_rate_in, 16000)
        self.assertEqual(cfg.live_voice.sample_rate_out, 24000)

    def test_02_env_overrides(self):
        with patch.dict(os.environ, {
            "JARVIS_LIVE_MODEL": "gemini-2.0-flash-realtime-exp",
            "JARVIS_LIVE_VOICE": "Puck",
            "JARVIS_LIVE_LOCATION": "global",
            "JARVIS_LIVE_NARRATE": "false",
            "JARVIS_LIVE": "1",
        }):
            cfg = load_config()
            self.assertEqual(cfg.live_voice.model, "gemini-2.0-flash-realtime-exp")
            self.assertEqual(cfg.live_voice.voice_name, "Puck")
            self.assertEqual(cfg.live_voice.location, "global")
            self.assertFalse(cfg.live_voice.narrate_steps)
            self.assertTrue(cfg.live_voice.enabled)


class AgentTelemetryTests(unittest.TestCase):
    """Test step progress telemetry in Agent.run."""

    def test_01_agent_run_emits_telemetry_events(self):
        cfg = Config()
        cfg.data.collect_trajectories = False
        cfg.data.verify_success = False
        brain = DummyBrain(cfg.brain)

        # Plan response: Step 1 click -> Step 2 finish
        brain.responses = [
            json.dumps({
                "thought": "I will click the search button.",
                "action": "click",
                "args": {"id": 5},
            }),
            json.dumps({
                "thought": "Task is completed.",
                "action": "finish",
                "args": {"message": "Found the results."},
            }),
        ]

        events = []
        def _on_progress(event):
            events.append(event)

        agent = Agent(brain, cfg)
        with patch.object(agent, "_perceive") as mock_perceive, \
             patch("jarvis.tools.registry.execute") as mock_exec:
            mock_obs = Mock()
            mock_obs.active_window = "Test Window"
            mock_obs.screen_size = (1920, 1080)
            mock_obs.elements = []
            mock_obs.menu.return_value = ""
            mock_perceive.return_value = mock_obs

            mock_res1 = Mock(ok=True, message="Clicked element 5", finished=False, ask=None, clear_image=False, image_path=None, needs_observe=True)
            mock_res2 = Mock(ok=True, message="Found the results.", finished=True, ask=None, clear_image=False, image_path=None, needs_observe=False)
            mock_exec.side_effect = [mock_res1, mock_res2]

            result = agent.run("click search and finish", on_progress=_on_progress)
            self.assertIn("Found the results", result)

        event_types = [e["event"] for e in events]
        self.assertIn("task_start", event_types)
        self.assertIn("plan_start", event_types)
        self.assertIn("step_start", event_types)
        self.assertIn("step_action", event_types)
        self.assertIn("step_result", event_types)
        self.assertIn("finish", event_types)

        # Verify step action details
        action_events = [e for e in events if e["event"] == "step_action"]
        self.assertEqual(action_events[0]["action"], "click")
        self.assertEqual(action_events[0]["args"], {"id": 5})

    def test_02_agent_run_emits_ask_and_cancelled_telemetry(self):
        cfg = Config()
        cfg.data.collect_trajectories = False
        brain = DummyBrain(cfg.brain)

        # Step 1 asks a question
        brain.responses = [
            json.dumps({
                "thought": "I need user input.",
                "action": "ask",
                "args": {"question": "Which folder would you like to use?"},
            })
        ]

        events = []
        agent = Agent(brain, cfg)
        with patch.object(agent, "_perceive") as mock_perceive, \
             patch("jarvis.tools.registry.execute") as mock_exec:
            mock_obs = Mock()
            mock_obs.active_window = "Test Window"
            mock_obs.screen_size = (1920, 1080)
            mock_obs.elements = []
            mock_obs.menu.return_value = ""
            mock_perceive.return_value = mock_obs

            mock_ask_res = Mock(ok=True, message="Asking user", finished=True, ask="Which folder would you like to use?", clear_image=False, image_path=None, needs_observe=False)
            mock_exec.return_value = mock_ask_res

            # No interactive asker -> ends with question
            res = agent.run("organize files", asker=None, on_progress=lambda e: events.append(e))
            self.assertEqual(res, "Which folder would you like to use?")

        event_types = [e["event"] for e in events]
        self.assertIn("ask", event_types)
        ask_event = next(e for e in events if e["event"] == "ask")
        self.assertEqual(ask_event["question"], "Which folder would you like to use?")

    def test_00b_agent_ask_with_interactive_answer(self):
        cfg = Config()
        cfg.data.collect_trajectories = False
        brain = DummyBrain(cfg.brain)

        # Step 1 asks a question; Step 2 completes task after receiving answer
        brain.responses = [
            json.dumps({
                "thought": "I need user input on which folder to use.",
                "action": "ask",
                "args": {"question": "Which folder would you like to use?"},
            }),
            json.dumps({
                "thought": "User answered Downloads. Finishing task.",
                "action": "finish",
                "args": {"summary": "Files organized in Downloads."},
            }),
        ]

        events = []
        agent = Agent(brain, cfg)
        recorded_messages = []

        with patch.object(agent, "_perceive") as mock_perceive, \
             patch("jarvis.tools.registry.execute") as mock_exec, \
             patch.object(agent, "_verify_success", return_value=(True, "Verified")):
            mock_obs = Mock()
            mock_obs.active_window = "Test Window"
            mock_obs.screen_size = (1920, 1080)
            mock_obs.elements = []
            mock_obs.menu.return_value = ""
            mock_perceive.return_value = mock_obs

            mock_ask_res = Mock(ok=True, message="Asking user", finished=True, ask="Which folder would you like to use?", clear_image=False, image_path=None, needs_observe=False)
            mock_finish_res = Mock(ok=True, message="Files organized in Downloads.", finished=True, ask=None, clear_image=False, image_path=None, needs_observe=False)
            mock_exec.side_effect = [mock_ask_res, mock_finish_res]

            # Interactive asker provides "Downloads"
            res = agent.run("organize files", asker=lambda q: "Downloads", on_progress=lambda e: events.append(e))
            self.assertEqual(res, "Files organized in Downloads.")

        event_types = [e["event"] for e in events]
        self.assertIn("ask", event_types)
        self.assertIn("answer_received", event_types)
        ans_event = next(e for e in events if e["event"] == "answer_received")
        self.assertEqual(ans_event["question"], "Which folder would you like to use?")
        self.assertEqual(ans_event["answer"], "Downloads")


class LiveAudioStreamTests(unittest.TestCase):
    """Test audio I/O, queueing, and barge-in management."""

    def test_01_audio_stream_init_and_properties(self):
        received_chunks = []
        barge_in_called = []

        stream = LiveAudioStream(
            rate_in=16000,
            rate_out=24000,
            on_audio_in=lambda c: received_chunks.append(c),
            on_barge_in=lambda: barge_in_called.append(True),
        )

        self.assertFalse(stream.is_running)
        self.assertFalse(stream.is_playing)

        # Test queueing playback chunk
        dummy_pcm = b"\x00\x00" * 480
        stream._is_running = True
        stream.play_chunk(dummy_pcm)
        self.assertFalse(stream._play_queue.empty())

        # Test clear playback
        stream.clear_playback()
        self.assertTrue(stream._play_queue.empty())
        self.assertFalse(stream.is_playing)


class GeminiLiveClientTests(unittest.TestCase):
    """Test Gemini Live WebSocket client message protocol and tool handling."""

    def test_01_setup_message_structure(self):
        cfg = LiveVoiceConfig(
            model="gemini-3.1-flash-live",
            voice_name="Aoede",
            location="us-central1",
        )
        client = GeminiLiveClient(cfg)
        setup = client._build_setup_message(project_id="test-proj-123")

        self.assertIn("setup", setup)
        model_uri = setup["setup"]["model"]
        self.assertEqual(model_uri, "projects/test-proj-123/locations/us-central1/publishers/google/models/gemini-3.1-flash-live")

        self.assertEqual(setup["setup"]["responseModalities"], ["AUDIO"])
        speech_cfg = setup["setup"]["speechConfig"]
        self.assertEqual(speech_cfg["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"], "Aoede")
        self.assertEqual(setup["setup"]["inputAudioTranscription"], {})
        self.assertEqual(setup["setup"]["outputAudioTranscription"], {})

        # Verify tool declarations
        tools = setup["setup"]["tools"]
        declarations = tools[0]["functionDeclarations"]
        tool_names = [d["name"] for d in declarations]
        self.assertIn("run_jarvis_task", tool_names)
        self.assertIn("cancel_task", tool_names)
        self.assertIn("ask_task_status", tool_names)

    def test_02_handle_server_content_audio_and_text(self):
        cfg = LiveVoiceConfig()
        audio_out = []
        text_out = []
        interrupted = []

        client = GeminiLiveClient(
            config=cfg,
            on_audio_out=lambda c: audio_out.append(c),
            on_text=lambda t: text_out.append(t),
            on_interrupted=lambda: interrupted.append(True),
        )

        test_pcm = b"\x12\x34\x56\x78"
        b64_pcm = base64.b64encode(test_pcm).decode("ascii")

        server_msg = {
            "serverContent": {
                "interrupted": True,
                "modelTurn": {
                    "parts": [
                        {"text": "Hello, I am Jarvis."},
                        {
                            "inlineData": {
                                "mimeType": "audio/pcm;rate=24000",
                                "data": b64_pcm,
                            }
                        },
                    ]
                },
                "turnComplete": True,
            }
        }

        client._handle_server_message(server_msg)
        self.assertTrue(interrupted)
        self.assertEqual(text_out, ["Hello, I am Jarvis."])
        self.assertEqual(audio_out, [test_pcm])

    def test_03_handle_tool_call(self):
        cfg = LiveVoiceConfig()
        tool_calls = []

        def _mock_tool_handler(name, args, call_id):
            tool_calls.append((name, args, call_id))
            return {"status": "started", "task_id": 1}

        client = GeminiLiveClient(
            config=cfg,
            on_tool_call=_mock_tool_handler,
        )

        tool_msg = {
            "toolCall": {
                "functionCalls": [
                    {
                        "name": "run_jarvis_task",
                        "args": {"task": "Open Chrome"},
                        "id": "call_abc123",
                    }
                ]
            }
        }

        client._handle_server_message(tool_msg)
        self.assertEqual(len(tool_calls), 1)
        name, args, call_id = tool_calls[0]
        self.assertEqual(name, "run_jarvis_task")
        self.assertEqual(args, {"task": "Open Chrome"})
        self.assertEqual(call_id, "call_abc123")

    def test_04_current_wire_messages_for_audio_text_and_tools(self):
        client = GeminiLiveClient(LiveVoiceConfig())
        sent = []
        client._is_connected = True
        client._loop = Mock()
        client._post_message = sent.append

        client.send_audio(b"\x01\x02")
        client.send_text_turn("Hello")
        client.send_tool_response("call_1", {"status": "started"}, "run_jarvis_task")

        self.assertEqual(sent[0]["realtimeInput"]["audio"]["mimeType"], "audio/pcm;rate=16000")
        self.assertNotIn("mediaChunks", sent[0]["realtimeInput"])
        self.assertEqual(sent[1], {"realtimeInput": {"text": "Hello"}})
        response = sent[2]["toolResponse"]["functionResponses"][0]
        self.assertEqual(response["name"], "run_jarvis_task")
        self.assertEqual(response["id"], "call_1")
        self.assertEqual(response["response"]["result"], {"status": "started"})

    def test_05_output_transcription_is_forwarded(self):
        text_out = []
        client = GeminiLiveClient(LiveVoiceConfig(), on_text=text_out.append)

        client._handle_server_message({
            "serverContent": {"outputTranscription": {"text": "Spoken reply"}}
        })

        self.assertEqual(text_out, ["Spoken reply"])


class LiveVoiceSupervisorTests(unittest.TestCase):
    """Test supervisor task orchestration, mid-task narration, and interactive questions."""

    def test_01_supervisor_launch_and_narration_sentence(self):
        cfg = Config()
        cfg.data.collect_trajectories = False
        brain = DummyBrain(cfg.brain)
        agent = Agent(brain, cfg)

        supervisor = LiveVoiceSupervisor(cfg, agent=agent)

        # Test natural narration sentence generation
        n1 = supervisor._build_narration_sentence(
            step=1,
            thought="Opening Chrome to navigate to web address.",
            action="launch",
            args={"app": "Chrome"},
        )
        self.assertIn("launching Chrome", n1)

        n2 = supervisor._build_narration_sentence(
            step=2,
            thought="Looking for the login form field.",
            action="click",
            args={"id": 14},
        )
        self.assertIn("clicking element 14", n2)

    def test_02_supervisor_task_launch_and_telemetry_monitoring(self):
        cfg = Config()
        cfg.data.collect_trajectories = False
        cfg.data.verify_success = False
        brain = DummyBrain(cfg.brain)

        brain.responses = [
            json.dumps({
                "thought": "Launching notepad to write notes.",
                "action": "launch",
                "args": {"app": "notepad"},
            }),
            json.dumps({
                "thought": "All notes written.",
                "action": "finish",
                "args": {"message": "Notepad opened and notes written."},
            }),
        ]

        agent = Agent(brain, cfg)
        supervisor = LiveVoiceSupervisor(cfg, agent=agent)

        with patch.object(agent, "_perceive") as mock_perceive, \
             patch("jarvis.tools.registry.execute") as mock_exec, \
             patch.object(supervisor, "_narrate_voice") as mock_narrate:
            mock_obs = Mock(active_window="Desktop", elements=[], menu=lambda: "")
            mock_perceive.return_value = mock_obs

            res1 = Mock(ok=True, message="Launched notepad", finished=False, ask=None, clear_image=False, image_path=None, needs_observe=True)
            res2 = Mock(ok=True, message="Done", finished=True, ask=None, clear_image=False, image_path=None, needs_observe=False)
            mock_exec.side_effect = [res1, res2]

            launch_res = supervisor.launch_task("Open notepad and write notes")
            self.assertEqual(launch_res["status"], "started")

            # Wait for background worker to complete
            if supervisor._active_task_thread:
                supervisor._active_task_thread.join(timeout=3.0)

            self.assertFalse(supervisor.is_task_running)
            self.assertTrue(mock_narrate.called)

    def test_03_supervisor_cancellation(self):
        cfg = Config()
        brain = DummyBrain(cfg.brain)
        agent = Agent(brain, cfg)
        supervisor = LiveVoiceSupervisor(cfg, agent=agent)

        # Launch a mock long running task
        supervisor._is_task_running = True
        supervisor._current_task = "Long automation task"

        cancel_res = supervisor.cancel_active_task()
        self.assertEqual(cancel_res["status"], "cancelled")
        self.assertFalse(supervisor.is_task_running)

    def test_04_supervisor_live_tool_dispatch(self):
        cfg = Config()
        brain = DummyBrain(cfg.brain)
        agent = Agent(brain, cfg)
        supervisor = LiveVoiceSupervisor(cfg, agent=agent)

        with patch.object(supervisor, "launch_task") as mock_launch, \
             patch.object(supervisor, "cancel_active_task") as mock_cancel:
            mock_launch.return_value = {"status": "started"}
            mock_cancel.return_value = {"status": "cancelled"}

            # 1. run_jarvis_task tool call
            r1 = supervisor._on_live_tool_call("run_jarvis_task", {"task": "Open Calculator"}, "id_1")
            mock_launch.assert_called_with("Open Calculator")

            # 2. cancel_task tool call
            r2 = supervisor._on_live_tool_call("cancel_task", {}, "id_2")
            mock_cancel.assert_called()

            # 3. ask_task_status tool call
            r3 = supervisor._on_live_tool_call("ask_task_status", {}, "id_3")
            self.assertIn("running", r3)


if __name__ == "__main__":
    unittest.main()
