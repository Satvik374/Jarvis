"""Tests for local Microsoft Foundry API Provider (Foundry Relay) integration."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch
import pytest

from jarvis.config import BrainConfig, VoiceConfig, load_config
from jarvis.agent.brain import AzureFoundryBrain, BrainError, make_brain
from jarvis.utils import voice


def test_foundry_relay_config_loading():
    """Verify load_config loads local Foundry relay configurations from env/config."""
    cfg = load_config()
    assert cfg.brain.model == "gpt-6"
    assert "localhost:8000" in cfg.brain.foundry_endpoint or "127.0.0.1:8000" in cfg.brain.foundry_endpoint
    assert "localhost:8000" in cfg.voice.tts_endpoint or "127.0.0.1:8000" in cfg.voice.tts_endpoint
    assert cfg.voice.tts_model == "tts-1"
    assert cfg.voice.tts_voice == "en-US-OnyxTurboMultilingualNeural"


def test_azure_foundry_brain_local_relay_detection():
    """Verify AzureFoundryBrain detects local relay endpoint and initializes OpenAI client."""
    bcfg = BrainConfig(
        backend="foundry",
        foundry_endpoint="http://localhost:8000/v1",
        foundry_agent_name="gpt-6",
        api_key="SATVIKNOOB",
    )
    brain = AzureFoundryBrain(bcfg)
    client = brain._get_client()
    assert brain._is_local_relay is True
    assert str(client.base_url).rstrip("/") == "http://localhost:8000/v1"


def test_azure_foundry_brain_local_relay_completion():
    """Verify completion on local relay calls responses.create."""
    bcfg = BrainConfig(
        backend="foundry",
        foundry_endpoint="http://localhost:8000/v1",
        foundry_agent_name="gpt-6",
        api_key="SATVIKNOOB",
    )
    brain = AzureFoundryBrain(bcfg)
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.output_text = "I am Jarvis running on Foundry Relay."
    mock_client.responses.create.return_value = mock_resp

    with patch.object(brain, "_get_client", return_value=mock_client):
        brain._is_local_relay = True
        ans = brain.complete("System prompt", [{"role": "user", "content": "Hello"}])
        assert ans == "I am Jarvis running on Foundry Relay."
        mock_client.responses.create.assert_called_once()


@pytest.fixture
def foundry_clients():
    """Never import real SDK/auth clients or inherit provider credentials."""
    openai = MagicMock()
    projects = MagicMock()
    auth = MagicMock()
    with patch.dict(os.environ, {}, clear=True), patch.dict(
        "sys.modules",
        {"openai": openai, "azure.ai.projects": projects, "jarvis.auth.azure_auth": auth},
    ):
        yield openai, projects, auth


@pytest.mark.parametrize("endpoint", [
    "http://localhost:8000/v1",
    "HTTPS://LOCALHOST:8000/v1",
    "https://127.0.0.1:8000/v1",
    "https://127.0.0.2:8000/v1",
    "https://[::1]:8000/v1",
    "http://relay.example:8000/v1",  # Existing remote HTTP relays remain supported.
])
def test_relay_parsed_host_detection(endpoint, foundry_clients):
    openai, projects, auth = foundry_clients
    brain = AzureFoundryBrain(BrainConfig(foundry_endpoint=endpoint, api_key="test-relay-key"))
    assert brain._get_client() is openai.OpenAI.return_value
    assert brain._get_client() is openai.OpenAI.return_value
    assert brain._is_local_relay is True
    openai.OpenAI.assert_called_once_with(base_url=endpoint, api_key="test-relay-key")
    projects.AIProjectClient.assert_not_called()
    auth.get_azure_credential.assert_not_called()


@pytest.mark.parametrize("endpoint", [
    "https://localhost.attacker.example/v1",
    "https://127.0.0.1.attacker.example/v1",
    "https://project.example/api/projects/localhost",
    "https://project.example/api/projects/127.0.0.1",
])
def test_cloud_endpoint_does_not_select_relay_by_substring(endpoint, foundry_clients):
    openai, projects, auth = foundry_clients
    os.environ["OPENAI_API_KEY"] = "test-unrelated-key"
    brain = AzureFoundryBrain(BrainConfig(foundry_endpoint=endpoint, api_key=""))
    client = brain._get_client()
    assert brain._is_local_relay is False
    openai.OpenAI.assert_not_called()
    projects.AIProjectClient.assert_called_once_with(
        endpoint=endpoint, credential=auth.get_azure_credential.return_value,
    )
    projects.AIProjectClient.return_value.get_openai_client.assert_called_once_with()
    assert client is projects.AIProjectClient.return_value.get_openai_client.return_value


@pytest.mark.parametrize("endpoint", [
    "localhost:8000/v1", "//localhost:8000/v1", "ftp://localhost/v1",
    "http:///v1", "http://:8000/v1", "http://localhost:bad/v1",
    "http://localhost:99999/v1", "http://[::1/v1",
    "https://localhost@attacker.example/v1", "http://user:pass@localhost/v1",
    "http://localhost\\\\@attacker.example/v1", "http://local\nhost/v1",
    "http://local host/v1", "https://localhost/v1?redirect=elsewhere",
    "https://localhost/v1#fragment",
])
def test_invalid_foundry_endpoint_rejected_before_credentials(endpoint, foundry_clients):
    openai, projects, auth = foundry_clients
    brain = AzureFoundryBrain(BrainConfig(foundry_endpoint=endpoint))
    with pytest.raises(BrainError, match="Invalid Foundry endpoint"):
        brain._get_client()
    openai.OpenAI.assert_not_called()
    projects.AIProjectClient.assert_not_called()
    auth.get_azure_credential.assert_not_called()
    assert brain._is_local_relay is False


def test_synthesize_openai_speech_mock():
    """Verify _synthesize_openai_speech posts correct payload and returns WAV bytes."""
    vcfg = VoiceConfig(
        engine="foundry",
        tts_endpoint="http://localhost:8000/v1/audio/speech",
        tts_model="tts-1",
        tts_voice="en-US-OnyxTurboMultilingualNeural",
        azure_speech_key="SATVIKNOOB",
    )
    mock_resp = MagicMock()
    mock_resp.read.return_value = b"RIFF_MOCK_WAV_BYTES"
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        result = voice._synthesize_openai_speech("Testing voice", vcfg)
        assert result == b"RIFF_MOCK_WAV_BYTES"
        mock_urlopen.assert_called_once()
