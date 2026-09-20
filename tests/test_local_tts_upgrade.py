"""Tests for Local TTS upgrade: default Kokoro engine, offline fallback, and speech normalization."""

import os
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

from jarvis.config import VoiceConfig, load_config
from jarvis.utils import voice


class LocalTTSUpgradeTests(unittest.TestCase):
    def tearDown(self):
        voice.reset()

    def test_default_engine_is_kokoro(self):
        """The built-in default is local Kokoro; a config file then the env override it.

        `load_config()` reads the checkout's own `config.yaml` and `.env`, and
        this project deliberately runs `engine: fish`, so an unqualified
        `load_config()` can never return the built-in default. What is worth
        pinning is the precedence order, with every ambient file pinned out.
        """
        cfg = VoiceConfig()
        self.assertEqual(cfg.engine, "kokoro")
        self.assertEqual(cfg.local_voice, "bm_george")
        self.assertEqual(cfg.local_speed, 1.0)

        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ), patch(
            "dotenv.load_dotenv"
        ):
            for k in ("JARVIS_TTS_ENGINE", "JARVIS_LOCAL_VOICE", "JARVIS_LOCAL_SPEED"):
                os.environ.pop(k, None)

            # Nothing set anywhere -> the built-in default.
            empty = Path(td) / "config.yaml"
            empty.write_text("", encoding="utf-8")
            self.assertEqual(load_config(empty).voice.engine, "kokoro")

            # A config file outranks the built-in default...
            fishy = Path(td) / "fish.yaml"
            fishy.write_text("voice:\n  engine: fish\n", encoding="utf-8")
            self.assertEqual(load_config(fishy).voice.engine, "fish")

            # ...and an explicit env var outranks the config file.
            with patch.dict(os.environ, {"JARVIS_TTS_ENGINE": "local"}):
                self.assertEqual(load_config(fishy).voice.engine, "kokoro")

    def test_local_engine_alias_and_env_overrides(self):
        """'local' alias maps to 'kokoro' and environment variables are respected."""
        with patch.dict(os.environ, {
            "JARVIS_TTS_ENGINE": "local",
            "JARVIS_LOCAL_VOICE": "bm_lewis",
            "JARVIS_LOCAL_SPEED": "1.25",
        }):
            loaded = load_config()
            self.assertEqual(loaded.voice.engine, "kokoro")
            self.assertEqual(loaded.voice.local_voice, "bm_lewis")
            self.assertEqual(loaded.voice.local_speed, 1.25)

    def test_clean_for_speech_normalizes_text(self):
        """_clean_for_speech strips emojis, agent labels, code blocks, and markdown."""
        raw = (
            "🎙️ [Communicating Agent]: Completed: **Task finished successfully!**\n"
            "Here is the python code:\n"
            "```python\nprint('hello world')\n```\n"
            "Please check [the documentation](https://example.com/docs/api).\n"
            "All systems operational. 🚀⚡"
        )
        cleaned = voice._clean_for_speech(raw)

        # Agent tag stripped
        self.assertNotIn("Communicating Agent", cleaned)
        self.assertNotIn("🎙️", cleaned)
        self.assertNotIn("🚀", cleaned)
        self.assertNotIn("⚡", cleaned)

        # Markdown bold stripped
        self.assertNotIn("**", cleaned)
        self.assertIn("Task finished successfully!", cleaned)

        # Code block converted to spoken placeholder
        self.assertNotIn("print('hello world')", cleaned)
        self.assertIn("[Code snippet provided]", cleaned)

        # Link URL simplified
        self.assertNotIn("https://", cleaned)
        self.assertIn("the documentation", cleaned)

    def test_clean_for_speech_handles_side_agent_and_questions(self):
        side_msg = "🎙️ [Side Agent]: Initiating plan: Search System Files."
        self.assertEqual(voice._clean_for_speech(side_msg), "Initiating plan: Search System Files.")

        q_msg = "⚠️ [Worker Question]: Do you want to proceed with deletion?"
        self.assertEqual(voice._clean_for_speech(q_msg), "Do you want to proceed with deletion?")

    def test_kokoro_local_speech_synthesis(self):
        """Kokoro synthesizes audio offline with no Gemini brain."""
        voice.configure(None, VoiceConfig(engine="kokoro"))
        with tempfile.TemporaryDirectory() as td:
            wav_path = Path(td) / "test_kokoro.wav"
            ok = voice.speak_to_wav("Good evening, sir. All systems are online.", str(wav_path))
            self.assertTrue(ok)
            self.assertTrue(wav_path.exists())
            with wave.open(str(wav_path), "rb") as wf:
                self.assertGreater(wf.getnframes(), 0)
                self.assertEqual(wf.getsampwidth(), 2)

    def test_sapi_local_speech_fallback(self):
        """Windows SAPI synthesizes 100% offline."""
        cfg = VoiceConfig(engine="sapi")
        wav_bytes = voice._synthesize_sapi("Testing offline Windows SAPI.", cfg)
        self.assertTrue(wav_bytes.startswith(b"RIFF"))
        self.assertIn(b"WAVE", wav_bytes[:16])

    def test_synthesize_wav_local_first_fallback(self):
        """When Kokoro fails, local TTS falls back cleanly to offline Windows SAPI."""
        voice.configure(None, VoiceConfig(engine="kokoro"))
        with patch.object(voice, "_synthesize_kokoro", side_effect=RuntimeError("ONNX Out of Memory")):
            wav_bytes = voice._synthesize_wav("Local fallback test message.")
            self.assertTrue(wav_bytes.startswith(b"RIFF"))
            self.assertIn(b"WAVE", wav_bytes[:16])


if __name__ == "__main__":
    unittest.main()
