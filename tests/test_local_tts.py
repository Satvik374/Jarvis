"""Local Kokoro TTS: synthesizes real audio offline, no Gemini brain needed."""

import time
import wave

import pytest

from jarvis.config import ROOT, VoiceConfig
from jarvis.utils import voice

_MODEL = ROOT / "models" / "tts" / "kokoro-v1.0.onnx"


@pytest.mark.skipif(not _MODEL.exists(), reason="kokoro model not downloaded")
def test_kokoro_speaks_offline(tmp_path):
    # kokoro engine, no brain: the local path can't need Gemini
    voice.configure(None, VoiceConfig(engine="kokoro"))
    out = tmp_path / "hello.wav"
    t0 = time.time()
    assert voice.speak_to_wav("Good evening, sir. All systems are online.",
                              str(out))
    elapsed = time.time() - t0
    with wave.open(str(out)) as w:
        secs = w.getnframes() / w.getframerate()
    assert secs > 1.0                      # it actually said something
    print(f"\n  synthesized {secs:.1f}s of speech in {elapsed:.1f}s "
          f"(RTF {elapsed / secs:.2f})")
    voice.reset()
