import base64
import contextlib
import os
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

from jarvis.agent.brain import GeminiVertexBrain
from jarvis.config import BrainConfig, VoiceConfig, load_config
from jarvis.utils import voice

# Every knob load_config() lets the environment override. Env beats config.yaml
# by design, so a test that reads a config file has to neutralise these first.
_ENV_OVERRIDES = (
    "JARVIS_MODEL", "MODEL_ID", "MODEL", "JARVIS_BACKEND", "BACKEND",
    "JARVIS_BASE_URL", "BASE_URL", "JARVIS_LOCATION", "LOCATION",
    "JARVIS_TTS_MODEL", "JARVIS_TTS_VOICE", "JARVIS_TTS_LANGUAGE",
    "JARVIS_STT_MODEL", "JARVIS_API_KEY", "API_KEY", "OPENAI_API_KEY",
)


@contextlib.contextmanager
def isolated_env():
    """Stop load_config() from reading the developer's real .env.

    Without this the assertions below silently depend on whoever's machine is
    running them - this test passed for months only because .env happened to
    name the same model, and started failing the moment that changed.
    """
    with patch("dotenv.load_dotenv", lambda *a, **k: None), \
            patch.dict(os.environ):          # snapshot; restored on exit
        for key in _ENV_OVERRIDES:
            os.environ.pop(key, None)
        yield


class GeminiTTSConfigTests(unittest.TestCase):
    def test_voice_model_is_configured_separately_from_thinking_model(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text(
                """
brain:
  backend: gemini
  model: gemini-3.5-flash
voice:
  model: gemini-3.1-flash-tts-preview
  voice: Kore
  language_code: en-US
""",
                encoding="utf-8",
            )

            with isolated_env():
                cfg = load_config(config_path)

        self.assertEqual(cfg.brain.model, "gemini-3.5-flash")
        self.assertEqual(cfg.voice.model, "gemini-3.1-flash-tts-preview")
        self.assertEqual(cfg.voice.voice, "Kore")
        self.assertEqual(cfg.voice.transcription_model, "gemini-2.5-flash-lite")

    def test_environment_overrides_the_config_file_model(self):
        """MODEL_ID wins over config.yaml. Pinned because it is a real
        footgun: editing brain.model in config.yaml looks like it does
        nothing while .env still names a different model."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text("brain:\n  model: from-yaml\n",
                                   encoding="utf-8")
            with isolated_env():
                os.environ["MODEL_ID"] = "from-env"
                cfg = load_config(config_path)
        self.assertEqual(cfg.brain.model, "from-env")


class GeminiVertexTTSRequestTests(unittest.TestCase):
    @patch("requests.post")
    def test_synthesis_uses_dedicated_tts_model_and_returns_pcm(self, post):
        pcm = b"\x01\x00\x02\x00"
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "inlineData": {
                                    "mimeType": "audio/L16;codec=pcm;rate=24000",
                                    "data": base64.b64encode(pcm).decode("ascii"),
                                }
                            }
                        ]
                    }
                }
            ]
        }
        post.return_value = response

        brain = GeminiVertexBrain(
            BrainConfig(model="gemini-3.5-flash", location="global")
        )
        brain._get_access_token_and_project = Mock(
            return_value=("access-token", "project-id")
        )

        result = brain.synthesize_speech(
            "Hello",
            model="gemini-3.1-flash-tts-preview",
            voice_name="Kore",
            language_code="en-US",
        )

        self.assertEqual(result, pcm)
        url = post.call_args.args[0]
        payload = post.call_args.kwargs["json"]
        self.assertIn("gemini-3.1-flash-tts-preview", url)
        self.assertNotIn("gemini-3.5-flash", url)
        self.assertEqual(brain.cfg.model, "gemini-3.5-flash")
        self.assertEqual(payload["generationConfig"]["responseModalities"], ["AUDIO"])
        self.assertEqual(
            payload["generationConfig"]["speechConfig"]["voiceConfig"]
            ["prebuiltVoiceConfig"]["voiceName"],
            "Kore",
        )

    @patch("requests.post")
    def test_transcription_uses_fast_model_without_changing_thinking_model(self, post):
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "candidates": [{
                "content": {"parts": [{"text": "Open Notepad"}]}
            }]
        }
        post.return_value = response

        brain = GeminiVertexBrain(
            BrainConfig(model="gemini-3.5-flash", location="global")
        )
        brain._get_access_token_and_project = Mock(
            return_value=("access-token", "project-id")
        )

        result = brain.transcribe_audio(
            b"wav-data", model="gemini-2.5-flash-lite"
        )

        self.assertEqual(result, "Open Notepad")
        url = post.call_args.args[0]
        payload = post.call_args.kwargs["json"]
        self.assertIn("gemini-2.5-flash-lite", url)
        self.assertNotIn("gemini-3.5-flash", url)
        self.assertEqual(
            payload["generationConfig"]["thinkingConfig"]["thinkingBudget"], 0
        )
        self.assertEqual(brain.cfg.model, "gemini-3.5-flash")


class VoiceOutputTests(unittest.TestCase):
    def tearDown(self):
        voice.reset()

    def test_speak_to_wav_wraps_gemini_pcm_as_24khz_mono(self):
        fake_brain = Mock()
        fake_brain.synthesize_speech.return_value = b"\x01\x00\x02\x00"
        voice.configure(
            fake_brain,
            VoiceConfig(
                engine="gemini",      # this test targets the cloud path
                model="gemini-3.1-flash-tts-preview",
                voice="Kore",
                language_code="en-US",
            ),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "speech.wav"
            self.assertTrue(voice.speak_to_wav("Hello", str(output)))
            with wave.open(str(output), "rb") as wav_file:
                self.assertEqual(wav_file.getnchannels(), 1)
                self.assertEqual(wav_file.getsampwidth(), 2)
                self.assertEqual(wav_file.getframerate(), 24000)
                self.assertEqual(wav_file.readframes(2), b"\x01\x00\x02\x00")

        fake_brain.synthesize_speech.assert_called_once_with(
            "Hello",
            model="gemini-3.1-flash-tts-preview",
            voice_name="Kore",
            language_code="en-US",
        )

    def test_transcribe_uses_configured_fast_model(self):
        fake_brain = Mock()
        fake_brain.transcribe_audio.return_value = "hello"
        voice.configure(
            fake_brain,
            VoiceConfig(transcription_model="gemini-2.5-flash-lite"),
        )

        self.assertEqual(voice.transcribe(b"wav-data", fake_brain), "hello")
        fake_brain.transcribe_audio.assert_called_once_with(
            b"wav-data", model="gemini-2.5-flash-lite"
        )


if __name__ == "__main__":
    unittest.main()
