"""Tests for Microsoft Azure Cognitive Services Speech SDK TTS Integration."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch
import pytest

from jarvis.config import VoiceConfig, BrainConfig, load_config
from jarvis.agent.brain import AzureFoundryBrain
from jarvis.utils import voice


def test_azure_speech_voice_config_defaults():
    """Verify default Azure Speech configuration in VoiceConfig."""
    cfg = VoiceConfig()
    assert cfg.azure_speech_endpoint == "https://satviksingh-resource.cognitiveservices.azure.com/"
    assert cfg.azure_speech_voice == "en-US-OnyxTurboMultilingualNeural"
    assert cfg.azure_speech_key == ""


def test_azure_speech_voice_config_env_overrides():
    """Verify environment variables override VoiceConfig."""
    env = {
        "JARVIS_TTS_ENGINE": "azure",
        "AZURE_SPEECH_KEY": "test_speech_key_12345",
        "AZURE_SPEECH_VOICE": "en-US-JennyNeural",
        "AZURE_SPEECH_ENDPOINT": "https://custom-resource.cognitiveservices.azure.com/",
    }
    with patch.dict(os.environ, env):
        cfg = load_config()
        assert cfg.voice.engine == "azure"
        assert cfg.voice.azure_speech_key == "test_speech_key_12345"
        assert cfg.voice.azure_speech_voice == "en-US-JennyNeural"
        assert cfg.voice.azure_speech_endpoint == "https://custom-resource.cognitiveservices.azure.com/"


def test_synthesize_azure_speech_success():
    """Verify _synthesize_azure_speech initializes SpeechConfig and returns audio bytes."""
    vcfg = VoiceConfig(
        engine="azure",
        azure_speech_key="mock_key_xyz",
        azure_speech_endpoint="https://satviksingh-resource.cognitiveservices.azure.com/",
        azure_speech_voice="en-US-OnyxTurboMultilingualNeural",
    )

    mock_speechsdk = MagicMock()
    mock_config = MagicMock()
    mock_speechsdk.SpeechConfig.return_value = mock_config
    mock_speechsdk.SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm = "Riff24Khz16BitMonoPcm"

    mock_synth = MagicMock()
    mock_result = MagicMock()
    mock_result.reason = mock_speechsdk.ResultReason.SynthesizingAudioCompleted
    mock_result.audio_data = b"RIFFmockwavdata24khz"

    mock_synth.speak_text_async.return_value.get.return_value = mock_result
    mock_speechsdk.SpeechSynthesizer.return_value = mock_synth

    mock_cog = MagicMock()
    mock_cog.speech = mock_speechsdk

    with patch.dict("sys.modules", {
        "azure.cognitiveservices": mock_cog,
        "azure.cognitiveservices.speech": mock_speechsdk,
    }):
        wav_bytes = voice._synthesize_azure_speech("Hello, welcome to Azure AI Foundry!", vcfg)

    assert wav_bytes == b"RIFFmockwavdata24khz"
    mock_speechsdk.SpeechConfig.assert_called_once_with(
        subscription="mock_key_xyz",
        endpoint="https://satviksingh-resource.cognitiveservices.azure.com",
    )
    assert mock_config.speech_synthesis_voice_name == "en-US-OnyxTurboMultilingualNeural"
    mock_config.set_speech_synthesis_output_format.assert_called_once_with("Riff24Khz16BitMonoPcm")
    mock_speechsdk.SpeechSynthesizer.assert_called_once_with(
        speech_config=mock_config, audio_config=None
    )


def test_synthesize_azure_speech_cancellation():
    """Verify _synthesize_azure_speech raises RuntimeError on cancellation."""
    vcfg = VoiceConfig(
        engine="azure",
        azure_speech_key="invalid_key",
        azure_speech_endpoint="https://satviksingh-resource.cognitiveservices.azure.com/",
    )

    mock_speechsdk = MagicMock()
    mock_result = MagicMock()
    mock_result.reason = mock_speechsdk.ResultReason.Canceled
    mock_result.cancellation_details.reason = mock_speechsdk.CancellationReason.Error
    mock_result.cancellation_details.error_details = "Authentication error (401)"

    mock_synth = MagicMock()
    mock_synth.speak_text_async.return_value.get.return_value = mock_result
    mock_speechsdk.SpeechSynthesizer.return_value = mock_synth

    mock_cog = MagicMock()
    mock_cog.speech = mock_speechsdk

    with patch.dict("sys.modules", {
        "azure.cognitiveservices": mock_cog,
        "azure.cognitiveservices.speech": mock_speechsdk,
    }):
        with pytest.raises(RuntimeError, match="Authentication error \\(401\\)"):
            voice._synthesize_azure_speech("Test error text", vcfg)


def test_synthesize_wav_azure_engine_preference():
    """Verify _synthesize_wav invokes Azure Speech when engine is 'azure'."""
    vcfg = VoiceConfig(
        engine="azure",
        azure_speech_key="valid_key",
    )
    voice.reset()

    fake_snapshot = MagicMock()
    fake_snapshot.config = vcfg
    fake_snapshot.epoch = voice._voice_epoch

    with patch.object(voice, "_synthesize_azure_speech", return_value=b"RIFF_AZURE_WAV") as mock_az:
        result = voice._synthesize_wav("Jarvis speaking", snapshot=fake_snapshot)

    assert result == b"RIFF_AZURE_WAV"
    mock_az.assert_called_once_with("Jarvis speaking", vcfg)


def test_azure_foundry_brain_synthesize_speech():
    """Verify AzureFoundryBrain.synthesize_speech delegates to _synthesize_azure_speech."""
    bcfg = BrainConfig(backend="foundry", model="gpt-6")
    brain = AzureFoundryBrain(bcfg)

    with patch("jarvis.utils.voice._synthesize_azure_speech", return_value=b"RIFF_BRAIN_AZURE_WAV") as mock_synth:
        res = brain.synthesize_speech("Greetings from GPT-6", voice_name="en-US-OnyxTurboMultilingualNeural")

    assert res == b"RIFF_BRAIN_AZURE_WAV"
    mock_synth.assert_called_once()
    args, kwargs = mock_synth.call_args
    assert args[0] == "Greetings from GPT-6"
    assert args[1].azure_speech_voice == "en-US-OnyxTurboMultilingualNeural"


def test_get_azure_speech_key_dynamic(tmp_path):
    """Verify _get_azure_speech_key_dynamic discovers key from .env file when env var is unset."""
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("AZURE_SPEECH_KEY", None)
        os.environ.pop("SPEECH_KEY", None)

        fake_env = tmp_path / ".env"
        fake_env.write_text("AZURE_SPEECH_KEY=dynamic_recovered_key_999\n", encoding="utf-8")

        with patch("jarvis.config.ROOT", tmp_path):
            key = voice._get_azure_speech_key_dynamic(VoiceConfig(azure_speech_key=""))
            assert key == "dynamic_recovered_key_999"
            assert os.environ.get("AZURE_SPEECH_KEY") == "dynamic_recovered_key_999"


def test_configure_resets_azure_speech_broken():
    """Verify configure() resets _azure_speech_broken so speech can recover."""
    voice._azure_speech_broken = True
    assert voice._azure_speech_broken is True

    voice.configure(None, VoiceConfig(engine="azure"))
    assert voice._azure_speech_broken is False

