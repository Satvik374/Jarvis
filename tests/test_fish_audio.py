"""Tests for Fish Audio TTS API Integration."""

from __future__ import annotations

import io
import os
from unittest.mock import MagicMock, patch
import urllib.error
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
