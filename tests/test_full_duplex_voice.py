import io
import struct
import threading
import time
import unittest
import wave
from unittest.mock import Mock, patch

from jarvis.config import Config, VoiceConfig
from jarvis.tools import registry
from jarvis.tools.schema import ACTIONS_BY_NAME
from jarvis.utils import voice
from jarvis.utils.voice import (
    _BargeInVAD,
    _EnergyVAD,
    _play_stream_interruptible,
    interrupt_speech,
    is_speaking,
    speak_and_listen,
)


def _generate_sine_wav(duration: float = 1.0, rate: int = 16000, freq: float = 440.0) -> bytes:
    """Generate a clean test WAV buffer with a sine wave."""
    import math
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        total_samples = int(rate * duration)
        samples = []
        for i in range(total_samples):
            val = int(32767.0 * 0.5 * math.sin(2.0 * math.pi * freq * i / rate))
            samples.append(val)
        wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buf.getvalue()


class FullDuplexVoiceTests(unittest.TestCase):
    def setUp(self):
        voice.reset()

    def tearDown(self):
        voice.interrupt_speech()
        voice.reset()

    def test_01_barge_in_vad_quiet_and_standard_speech(self):
        # 1. Steady fan noise during listening: does not start
        vad = _BargeInVAD(silence_after=0.6, barge_in_sensitivity=0.5)
        fan_levels = [400, 420, 390, 410, 405] * 3
        for lv in fan_levels:
            state = vad.feed(lv, is_speaking=False)
            self.assertEqual(state, "listening")

        # 2. Real user speech occurs when not speaking: triggers standard recording
        speech_levels = [2500, 2600, 2400]
        state = None
        for lv in speech_levels:
            state = vad.feed(lv, is_speaking=False)
        self.assertEqual(state, "recording")

    def test_02_barge_in_vad_speaker_bleed_vs_user_interruption(self):
        # When Jarvis is actively speaking (is_speaking=True), speaker bleed (e.g. level ~1200)
        # must NOT trigger interruption, but loud human voice (e.g. level ~3500) MUST trigger "interrupt"!
        vad = _BargeInVAD(silence_after=0.6, barge_in_sensitivity=0.5, barge_in_hold=2)

        # Baseline noise floor settling (~300)
        for _ in range(6):
            vad.feed(300, is_speaking=False)

        # Moderate speaker echo bleed (~900, 3x floor) while speaking: should stay 'listening'
        s1 = vad.feed(900, is_speaking=True)
        s2 = vad.feed(950, is_speaking=True)
        self.assertEqual(s1, "listening")
        self.assertEqual(s2, "listening")

        # Strong human barge-in voice (~3500, >10x floor) for 2 chunks: must return "interrupt"!
        vad.feed(3500, is_speaking=True)
        state_int = vad.feed(3600, is_speaking=True)
        self.assertEqual(state_int, "interrupt")

    def test_03_interrupt_speech_and_is_speaking_state(self):
        self.assertFalse(is_speaking())

        # Test interrupt_speech when not speaking returns False
        was_spk = interrupt_speech()
        self.assertFalse(was_spk)

    def test_04_streaming_playback_interruption(self):
        # Generate 2.0s audio clip
        wav_data = _generate_sine_wav(duration=2.0)
        cancel_event = threading.Event()

        # Trigger cancel_event after 100ms
        def _cancel_worker():
            time.sleep(0.1)
            cancel_event.set()

        t = threading.Thread(target=_cancel_worker, daemon=True)
        t.start()

        start_t = time.time()
        was_interrupted, played_sec = _play_stream_interruptible(wav_data, cancel_event=cancel_event)
        elapsed = time.time() - start_t

        self.assertTrue(was_interrupted)
        self.assertLess(played_sec, 1.0)  # Interrupted long before 2.0s
        t.join(timeout=1.0)

    def test_05_voice_control_tool_action(self):
        self.assertIn("voice_control", ACTIONS_BY_NAME)
        cfg = Config(voice=VoiceConfig(full_duplex=True, barge_in_sensitivity=0.5))

        # Status
        r1 = registry.execute("voice_control", {"action": "status"}, None, cfg)
        self.assertTrue(r1.ok)
        self.assertIn("full_duplex=True", r1.message)

        # Disable duplex
        r2 = registry.execute("voice_control", {"action": "disable_duplex"}, None, cfg)
        self.assertTrue(r2.ok)
        self.assertFalse(cfg.voice.full_duplex)

        # Enable duplex
        r3 = registry.execute("voice_control", {"action": "enable_duplex"}, None, cfg)
        self.assertTrue(r3.ok)
        self.assertTrue(cfg.voice.full_duplex)

        # Set sensitivity
        r4 = registry.execute("voice_control", {"action": "set_sensitivity", "value": "0.85"}, None, cfg)
        self.assertTrue(r4.ok)
        self.assertAlmostEqual(cfg.voice.barge_in_sensitivity, 0.85)

        # Interrupt
        r5 = registry.execute("voice_control", {"action": "interrupt"}, None, cfg)
        self.assertTrue(r5.ok)

    def test_06_edge_tts_synthesis(self):
        cfg = VoiceConfig(engine="edge", voice="Ryan")
        wav = voice._synthesize_edge_tts("Hello testing Edge TTS.", cfg)
        self.assertTrue(wav.startswith(b"RIFF"))
        self.assertIn(b"WAVE", wav[:16])

    def test_07_sapi_tts_synthesis(self):
        cfg = VoiceConfig(engine="sapi")
        wav = voice._synthesize_sapi("Testing SAPI fallback.", cfg)
        self.assertTrue(wav.startswith(b"RIFF"))
        self.assertIn(b"WAVE", wav[:16])

    def test_08_multi_tier_fallback_on_gemini_failure(self):
        # Configure Gemini as default engine
        cfg = VoiceConfig(engine="gemini")
        mock_brain = Mock()
        # Make brain raise Vertex AI Connection failure
        mock_brain.synthesize_speech.side_effect = RuntimeError(
            "Vertex AI Gemini TTS request failed: HTTPSConnectionPool(host='aiplatform.googleapis.com')"
        )
        voice.configure(brain=mock_brain, config=cfg)

        # Call synthesize_wav: should cleanly catch Gemini error and fall back to Edge-TTS / SAPI
        wav_bytes = voice._synthesize_wav("Jarvis fallback test message.")
        self.assertTrue(wav_bytes.startswith(b"RIFF"))
        self.assertIn(b"WAVE", wav_bytes[:16])
        self.assertGreater(len(wav_bytes), 1000)


if __name__ == "__main__":
    unittest.main()
