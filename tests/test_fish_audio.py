"""Tests for Fish Audio TTS API Integration."""

from __future__ import annotations

import io
import os
import struct
import sys
from unittest.mock import MagicMock, patch
import urllib.error
import wave
import pytest

from jarvis.config import VoiceConfig, load_config
from jarvis.utils import voice


def test_fish_audio_config_defaults():
    """Verify default Fish Audio configuration in VoiceConfig."""
    cfg = VoiceConfig()
    assert cfg.fish_audio_key == ""
    assert cfg.fish_audio_voice_id == ""
    assert cfg.fish_audio_model == "s2.1-pro"
    assert cfg.fish_audio_latency == "normal"


def test_fish_audio_config_env_overrides():
    """Verify environment variables override Fish Audio settings."""
    env = {
        "JARVIS_TTS_ENGINE": "fish",
        "FISH_AUDIO_API_KEY": "fish_test_secret_key_123",
        "FISH_AUDIO_VOICE_ID": "voice_ref_abc",
        "FISH_AUDIO_MODEL": "s2.1-pro",
        "FISH_AUDIO_LATENCY": "balanced",
    }
    with patch.dict(os.environ, env):
        cfg = load_config()
        assert cfg.voice.engine == "fish"
        assert cfg.voice.fish_audio_key == "fish_test_secret_key_123"
        assert cfg.voice.fish_audio_voice_id == "voice_ref_abc"
        assert cfg.voice.fish_audio_model == "s2.1-pro"
        assert cfg.voice.fish_audio_latency == "balanced"


def test_get_fish_audio_key_dynamic_priority():
    """Verify key resolution priority from config, env, and vault."""
    cfg = VoiceConfig(fish_audio_key="cfg_key")
    assert voice._get_fish_audio_key_dynamic(cfg) == "cfg_key"

    # From environment
    cfg_empty = VoiceConfig()
    with patch.dict(os.environ, {"FISH_AUDIO_API_KEY": "env_fish_key"}):
        assert voice._get_fish_audio_key_dynamic(cfg_empty) == "env_fish_key"

    # From vault fallback
    with patch.dict(os.environ, {}, clear=True):
        with patch("pathlib.Path.exists", return_value=False):
            with patch("jarvis.security.get_secret", return_value="vault_fish_key"):
                assert voice._get_fish_audio_key_dynamic(cfg_empty) == "vault_fish_key"


def test_synthesize_fish_audio_success():
    """Verify _synthesize_fish_audio sends correct POST payload and headers."""
    cfg = VoiceConfig(
        engine="fish",
        fish_audio_key="mock_fish_key",
        fish_audio_voice_id="custom_voice_id",
        fish_audio_model="s2.1-pro",
        fish_audio_latency="normal",
    )

    mock_resp = MagicMock()
    mock_resp.read.return_value = b"RIFF\x24\x00\x00\x00WAVEfmt mock_wav_bytes"
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None

    captured_req = []

    def fake_urlopen(req, timeout=None):
        captured_req.append(req)
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        audio_bytes = voice._synthesize_fish_audio("Hello from Jarvis", cfg)
        assert audio_bytes.startswith(b"RIFF")

    assert len(captured_req) == 1
    req = captured_req[0]
    assert req.full_url == "https://api.fish.audio/v1/tts"
    assert req.headers["Authorization"] == "Bearer mock_fish_key"
    assert req.headers["Content-type"] == "application/json"
    assert req.headers["Model"] == "s2.1-pro"

    import json
    payload = json.loads(req.data.decode("utf-8"))
    assert payload["text"] == "Hello from Jarvis"
    assert payload["format"] == "wav"
    assert payload["reference_id"] == "custom_voice_id"
    assert payload["latency"] == "normal"


def test_synthesize_fish_audio_missing_key():
    """Verify RuntimeError is raised when no Fish Audio API key is provided."""
    cfg = VoiceConfig(engine="fish", fish_audio_key="")
    with patch.dict(os.environ, {}, clear=True):
        with patch("pathlib.Path.exists", return_value=False):
            with patch("jarvis.security.get_secret", return_value=""):
                with pytest.raises(RuntimeError, match="Fish Audio API key is required"):
                    voice._synthesize_fish_audio("Test missing key", cfg)


def test_synthesize_fish_audio_http_error():
    """Verify HTTP errors raise informative RuntimeError."""
    cfg = VoiceConfig(engine="fish", fish_audio_key="mock_key")

    fp = io.BytesIO(b'{"detail":"Invalid API Key"}')
    mock_err = urllib.error.HTTPError(
        url="https://api.fish.audio/v1/tts",
        code=401,
        msg="Unauthorized",
        hdrs={},
        fp=fp,
    )

    with patch("urllib.request.urlopen", side_effect=mock_err):
        with pytest.raises(RuntimeError, match="Fish Audio API error \\(401\\)"):
            voice._synthesize_fish_audio("Test error", cfg)


def test_synthesize_wav_fish_routing_and_fallback():
    """Verify _synthesize_wav routes to Fish Audio and cascades gracefully on failure."""
    cfg = VoiceConfig(engine="fish", fish_audio_key="mock_key")
    voice.reset()

    # Success case
    with patch.object(voice, "_synthesize_fish_audio", return_value=b"RIFF_FISH_WAV") as mock_fish:
        with patch.object(voice, "_current_voice_snapshot") as mock_snap:
            snapshot = MagicMock()
            snapshot.config = cfg
            snapshot.epoch = voice._voice_epoch
            mock_snap.return_value = snapshot

            wav = voice._synthesize_wav("Testing Fish Audio")
            assert wav == b"RIFF_FISH_WAV"
            mock_fish.assert_called_once()

    # Failure & fallback case
    voice.reset()
    with patch.object(voice, "_synthesize_fish_audio", side_effect=RuntimeError("Quota exceeded")):
        with patch.object(voice, "_synthesize_kokoro", return_value=b"RIFF_KOKORO_FALLBACK") as mock_kokoro:
            with patch.object(voice, "_current_voice_snapshot") as mock_snap:
                snapshot = MagicMock()
                snapshot.config = cfg
                snapshot.epoch = voice._voice_epoch
                mock_snap.return_value = snapshot

                wav = voice._synthesize_wav("Testing Fallback")
                assert wav == b"RIFF_KOKORO_FALLBACK"
                mock_kokoro.assert_called_once()


def _placeholder_wav(pcm: bytes, rate: int = 44100) -> bytes:
    """The exact header Fish Audio returns: both sizes still at their stream placeholders."""
    return (
        b"RIFF" + (0xFFFFFF24).to_bytes(4, "little") + b"WAVE"
        + b"fmt " + (16).to_bytes(4, "little")
        + (1).to_bytes(2, "little") + (1).to_bytes(2, "little")
        + rate.to_bytes(4, "little") + (rate * 2).to_bytes(4, "little")
        + (2).to_bytes(2, "little") + (16).to_bytes(2, "little")
        + b"data" + (0xFFFFFF00).to_bytes(4, "little")
        + pcm
    )


def test_a_placeholder_wav_is_made_to_describe_the_audio_it_carries():
    """A stream placeholder header must not be read as fifteen hours of audio."""
    pcm = b"\x01\x02" * 44100        # one second at 44.1 kHz, mono, 16-bit
    repaired = voice._repair_wav_sizes(_placeholder_wav(pcm))

    assert struct.unpack("<L", repaired[4:8])[0] == len(repaired) - 8
    assert struct.unpack("<L", repaired[40:44])[0] == len(pcm)
    with wave.open(io.BytesIO(repaired), "rb") as handle:
        assert handle.getnframes() / handle.getframerate() == pytest.approx(1.0)
    # The audio is untouched, and repairing an already repaired clip changes nothing.
    assert repaired[44:] == pcm
    assert voice._repair_wav_sizes(repaired) == repaired


def test_repair_leaves_anything_that_is_not_a_sized_wav_alone():
    assert voice._repair_wav_sizes(b"") == b""
    assert voice._repair_wav_sizes(b"RIFF_fish_wav") == b"RIFF_fish_wav"
    # Chunks that do not parse must survive verbatim rather than be guessed at.
    odd = b"RIFF\x24\x00\x00\x00WAVEfmt mock_wav_bytes"
    assert voice._repair_wav_sizes(odd) == odd


def test_the_api_response_is_repaired_on_its_way_out():
    cfg = VoiceConfig(engine="fish", fish_audio_key="mock_fish_key")
    mock_resp = MagicMock()
    mock_resp.read.return_value = _placeholder_wav(b"\x00\x01" * 22050)
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None

    with patch("urllib.request.urlopen", return_value=mock_resp):
        audio = voice._synthesize_fish_audio("Hello from Jarvis", cfg)

    assert audio.startswith(b"RIFF")
    assert struct.unpack("<L", audio[40:44])[0] == len(audio) - 44


def test_playback_never_hands_windows_a_placeholder_length():
    """winsound walks the declared length: an unrepaired header is a crash, not an error."""
    handed = []

    class FakeWinsound:
        SND_MEMORY = 4
        SND_NODEFAULT = 8
        SND_FILENAME = 0x20000
        SND_ASYNC = 1

        @staticmethod
        def PlaySound(sound, flags):
            if isinstance(sound, bytes):
                handed.append(sound)

    with patch.dict(sys.modules, {"winsound": FakeWinsound}):
        voice._play_wav(_placeholder_wav(b"\x00\x01" * 22050), wait=True)

    assert handed, "the clip never reached the player"
    assert struct.unpack("<L", handed[0][40:44])[0] == len(handed[0]) - 44
    assert struct.unpack("<L", handed[0][4:8])[0] == len(handed[0]) - 8
