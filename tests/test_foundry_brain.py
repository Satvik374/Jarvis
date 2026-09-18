"""Tests for Microsoft Azure AI Foundry Agent (GPT-6 Astra) Brain backend."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest
from PIL import Image

from jarvis.config import BrainConfig, Config
from jarvis.agent.brain import AzureFoundryBrain, make_brain, BrainError, complete_with_retry


def test_make_brain_foundry_backend():
    """Verify make_brain constructs AzureFoundryBrain for foundry backends."""
    for backend_name in ("foundry", "azure", "azure-foundry", "azure_foundry", "foundry-agent", "gpt-6"):
        cfg = BrainConfig(backend=backend_name, model="gpt-6")
        brain = make_brain(cfg)
        assert isinstance(brain, AzureFoundryBrain)
        assert brain.agent_name == "gpt-6"
        assert brain.agent_version == "1"
        assert "satviksingh-resource" in brain.endpoint


def test_foundry_brain_custom_config():
    """Verify custom endpoint, agent name, and version are respected."""
    cfg = BrainConfig(
        backend="foundry",
        foundry_endpoint="https://custom-resource.services.ai.azure.com/api/projects/myproj",
        foundry_agent_name="gpt-6-astra",
        foundry_agent_version="2",
        azure_tenant_id="tenant-12345",
    )
    brain = AzureFoundryBrain(cfg)
    assert brain.endpoint == "https://custom-resource.services.ai.azure.com/api/projects/myproj"
    assert brain.agent_name == "gpt-6-astra"
    assert brain.agent_version == "2"
    assert brain.tenant_id == "tenant-12345"


def test_foundry_brain_complete_basic():
    """Verify brain.complete formats messages and invokes responses.create."""
    cfg = BrainConfig(backend="foundry", model="gpt-6", temperature=0.3, max_tokens=2048)
    brain = AzureFoundryBrain(cfg)

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.output_text = '{"thought": "Hello", "action": "answer", "args": {"text": "Hi there!"}}'
    mock_client.responses.create.return_value = mock_resp

    with patch.object(brain, "_get_client", return_value=mock_client):
        result = brain.complete(
            system="You are JARVIS.",
            messages=[
                {"role": "user", "content": "Hello!"},
                {"role": "assistant", "content": "Greetings!"},
                {"role": "user", "content": "How are you?"},
            ],
        )

    assert result == '{"thought": "Hello", "action": "answer", "args": {"text": "Hi there!"}}'
    mock_client.responses.create.assert_called_once()
    call_kwargs = mock_client.responses.create.call_args[1]

    # Azure Foundry Agent API prohibits instructions & temperature at request level
    assert "instructions" not in call_kwargs
    assert "temperature" not in call_kwargs
    assert call_kwargs["extra_body"] == {
        "agent_reference": {
            "name": "gpt-6",
            "version": "1",
            "type": "agent_reference",
        }
    }
    input_items = call_kwargs["input"]
    assert len(input_items) == 3
    assert input_items[0]["role"] == "user"
    assert isinstance(input_items[0]["content"], str)
    assert "You are JARVIS." in input_items[0]["content"]
    assert "Hello!" in input_items[0]["content"]
    assert input_items[1]["role"] == "assistant"
    assert input_items[1]["content"] == "Greetings!"
    assert input_items[2]["role"] == "user"
    assert input_items[2]["content"] == "How are you?"


def test_foundry_brain_deep_output_extraction():
    """Verify deep output extraction if output_text is None."""
    cfg = BrainConfig(backend="foundry", model="gpt-6")
    brain = AzureFoundryBrain(cfg)

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.output_text = None

    # Simulate response.output with nested text parts
    part1 = MagicMock(text='{"action": "click", ')
    part2 = MagicMock(text='"args": {"id": 5}}')
    mock_output_item = MagicMock(content=[part1, part2])
    mock_resp.output = [mock_output_item]
    mock_client.responses.create.return_value = mock_resp

    with patch.object(brain, "_get_client", return_value=mock_client):
        result = brain.complete(system="Sys", messages=[{"role": "user", "content": "Click 5"}])

    assert result == '{"action": "click", "args": {"id": 5}}'


def test_foundry_brain_vision_support():
    """Verify vision mode encodes PIL images into input_image parts."""
    cfg = BrainConfig(backend="foundry", model="gpt-6", use_vision=True)
    brain = AzureFoundryBrain(cfg)

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.output_text = '{"thought": "Saw button", "action": "click", "args": {"id": 1}}'
    mock_client.responses.create.return_value = mock_resp

    test_image = Image.new("RGB", (100, 100), color="blue")

    with patch.object(brain, "_get_client", return_value=mock_client):
        result = brain.complete(
            system="Vision system",
            messages=[{"role": "user", "content": "What is on screen?"}],
            image=test_image,
        )

    assert "click" in result
    call_kwargs = mock_client.responses.create.call_args[1]
    input_items = call_kwargs["input"]
    last_content = input_items[-1]["content"]

    # Must contain both input_text and input_image
    types = [item.get("type") for item in last_content if isinstance(item, dict)]
    assert "input_text" in types
    assert "input_image" in types


def test_foundry_brain_vision_fallback_on_rejection():
    """Verify graceful fallback to text-only if the agent rejects images."""
    cfg = BrainConfig(backend="foundry", model="gpt-6", use_vision=True)
    brain = AzureFoundryBrain(cfg)

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.output_text = "Fallback text response"

    # First call fails with image rejection, second call succeeds
    mock_client.responses.create.side_effect = [
        RuntimeError("Image input is unsupported for this agent"),
        mock_resp,
    ]

    test_image = Image.new("RGB", (50, 50), color="red")

    with patch.object(brain, "_get_client", return_value=mock_client):
        result = brain.complete(
            system="Vision system",
            messages=[{"role": "user", "content": "Describe screen"}],
            image=test_image,
        )

    assert result == "Fallback text response"
    assert brain.cfg.use_vision is False
    assert mock_client.responses.create.call_count == 2


@pytest.mark.parametrize("response", [
    SimpleNamespace(output_text=None, output=[]),
    SimpleNamespace(output_text="   ", output=[]),
    SimpleNamespace(output_text=None, output=[SimpleNamespace(content=[SimpleNamespace(text="  ")])]),
])
def test_foundry_empty_output_is_retryable_error(response):
    brain = AzureFoundryBrain(BrainConfig(backend="foundry"))
    client = MagicMock()
    client.responses.create.return_value = response
    with patch.object(brain, "_get_client", return_value=client):
        with pytest.raises(BrainError, match="empty content"):
            brain.complete("System", [{"role": "user", "content": "Hello"}])


def test_foundry_empty_output_retries_then_recovers():
    brain = AzureFoundryBrain(BrainConfig(backend="foundry"))
    client = MagicMock()
    client.responses.create.side_effect = [
        SimpleNamespace(output_text=None, output=[]),
        SimpleNamespace(output_text="Recovered"),
    ]
    with patch.object(brain, "_get_client", return_value=client), patch("jarvis.agent.brain.time.sleep") as sleep:
        assert complete_with_retry(brain, "System", [{"role": "user", "content": "Hello"}]) == "Recovered"
    assert client.responses.create.call_count == 2
    sleep.assert_called_once_with(5)


@pytest.mark.parametrize("relay", [False, True])
def test_foundry_keyboard_interrupt_is_not_swallowed(relay):
    brain = AzureFoundryBrain(BrainConfig(backend="foundry"))
    brain._is_local_relay = relay
    client = MagicMock()
    client.responses.create.side_effect = KeyboardInterrupt
    with patch.object(brain, "_get_client", return_value=client):
        with pytest.raises(KeyboardInterrupt):
            brain.complete("System", [{"role": "user", "content": "Hello"}])
    client.chat.completions.create.assert_not_called()
