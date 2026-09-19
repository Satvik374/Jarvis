"""OpenRouter as the brain backend.

The OpenRouter path is the generic OpenAI-compatible one, so these tests pin the
three things that are actually OpenRouter-specific and easy to regress: the
endpoint/attribute headers it requires, the fact that a `:free` text-only model
must not be sent a screenshot, and that a rejected request reports the
provider's own words instead of requests' bare status line.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
from unittest.mock import Mock, patch

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import run  # noqa: E402
from jarvis.agent.brain import (  # noqa: E402
    BrainError,
    OpenAICompatBrain,
    make_brain,
    provider_error_message,
)
from jarvis.config import BrainConfig, Config, load_config  # noqa: E402

MODEL = "deepseek/deepseek-v4-flash-0731:free"


@pytest.fixture(autouse=True)
def _isolated_environment():
    """`load_config` writes key aliases straight into os.environ; undo them.

    It copies a configured key to OPENAI_API_KEY (and back), so without this a
    test that loads a config would leave another test's key visible to the brain
    under a name it never set itself.
    """
    before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(before)


def _plain(output: str) -> str:
    """Console output without ANSI colour, line wrapping or indentation."""
    return " ".join(ANSI_RE.sub("", output).split())


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class _Response:
    """A requests.Response stand-in that is honest about status and body."""

    def __init__(self, status_code: int = 200, body=None, text: str = ""):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._body = body
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError("no JSON body")
        return self._body


def _ok_response(content: str = '{"action": "finish"}') -> _Response:
    return _Response(body={"choices": [{"message": {"content": content}}]})


def _brain(**overrides) -> OpenAICompatBrain:
    cfg = BrainConfig(
        backend="openrouter",
        model=MODEL,
        base_url="https://openrouter.ai/api/v1",
        api_key="sk-or-test",
        use_vision=True,
        **overrides,
    )
    return OpenAICompatBrain(cfg)


def test_make_brain_selects_the_openai_compatible_transport():
    cfg = BrainConfig(backend="openrouter", model=MODEL, api_key="sk-or-test")
    brain = make_brain(cfg)

    assert isinstance(brain, OpenAICompatBrain)
    assert cfg.base_url == "https://openrouter.ai/api/v1"
    assert cfg.api_key_env == "OPENROUTER_API_KEY"


def test_request_carries_openrouter_headers():
    brain = _brain()
    brain._http_post = Mock(return_value=_ok_response())

    assert brain.complete("system prompt", [{"role": "user", "content": "task"}]) == '{"action": "finish"}'

    url, kwargs = brain._http_post.call_args.args[0], brain._http_post.call_args.kwargs
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert kwargs["json"]["model"] == MODEL
    assert kwargs["headers"]["Authorization"] == "Bearer sk-or-test"
    # OpenRouter attributes traffic with these two; they are not optional extras.
    assert kwargs["headers"]["HTTP-Referer"].startswith("https://")
    assert kwargs["headers"]["X-Title"] == "Jarvis Desktop Assistant"


def test_text_only_model_is_retried_without_the_screenshot():
    """A free DeepSeek model takes text only; the call must not die on that."""
    brain = _brain()
    rejected = _Response(
        status_code=404,
        body={"error": {"message": "No endpoints found that support image input"}},
    )
    pending = [rejected, _ok_response("done")]
    sent: list[str] = []

    def _post(url, **kwargs):
        # The fallback edits the payload dict in place, so snapshot it here.
        sent.append(json.dumps(kwargs["json"]))
        return pending.pop(0)

    brain._http_post = _post

    image = Image.new("RGB", (16, 16), "white")
    assert brain.complete("system", [{"role": "user", "content": "task"}], image=image) == "done"

    assert len(sent) == 2
    assert "image_url" in sent[0]
    assert "image_url" not in sent[1]
    # Learned once, so the next step of the task does not pay for it again.
    assert brain.cfg.use_vision is False


def test_vision_stays_on_when_the_provider_error_is_unrelated():
    brain = _brain()
    brain._http_post = Mock(return_value=_Response(
        status_code=429,
        body={"error": {"message": "Rate limit exceeded: free-models-per-day"}},
    ))

    with pytest.raises(BrainError):
        brain.complete("system", [{"role": "user", "content": "task"}],
                       image=Image.new("RGB", (16, 16), "white"))

    assert brain.cfg.use_vision is True


def test_rejected_request_reports_the_provider_message_and_a_remedy():
    brain = _brain()
    brain._http_post = Mock(return_value=_Response(
        status_code=402,
        body={"error": {"message": "Insufficient credits to run this model"}},
    ))

    with pytest.raises(BrainError) as excinfo:
        brain.complete("system", [{"role": "user", "content": "task"}])

    message = str(excinfo.value)
    assert "Insufficient credits to run this model" in message
    assert MODEL in message
    assert "402" in message
    assert "no credit left" in message


def test_provider_error_message_reads_openrouter_error_shapes():
    assert provider_error_message(
        _Response(status_code=400, body={"error": {"message": "bad request"}})
    ) == "bad request"
    # No JSON body at all - fall back to the raw text rather than nothing.
    assert provider_error_message(
        _Response(status_code=500, body=None, text="upstream exploded")
    ) == "upstream exploded"
    assert provider_error_message(_Response(status_code=500, body={}, text="")) == ""


def test_config_fills_openrouter_defaults_left_blank_in_yaml(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(
        "brain:\n"
        "  backend: openrouter\n"
        f"  model: {MODEL}\n"
        "  base_url: \"\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JARVIS_BACKEND", "openrouter")
    monkeypatch.setenv("JARVIS_MODEL", MODEL)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    for name in ("JARVIS_BASE_URL", "BASE_URL", "JARVIS_API_KEY", "API_KEY"):
        monkeypatch.delenv(name, raising=False)

    cfg = load_config(path)

    assert cfg.brain.backend == "openrouter"
    assert cfg.brain.model == MODEL
    assert cfg.brain.base_url == "https://openrouter.ai/api/v1"
    assert cfg.brain.api_key_env == "OPENROUTER_API_KEY"
    assert cfg.brain.api_key == "sk-or-test"


def test_openrouter_key_from_environment_reaches_a_real_brain_call(monkeypatch):
    cfg = BrainConfig(backend="openrouter", model=MODEL, base_url="https://openrouter.ai/api/v1")
    brain = OpenAICompatBrain(cfg)
    brain._http_post = Mock(return_value=_ok_response())
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-from-env")

    brain.complete("system", [{"role": "user", "content": "task"}])

    assert brain._http_post.call_args.kwargs["headers"]["Authorization"] == "Bearer sk-or-from-env"


# --------------------------------------------------------------------------- #
# `python run.py --check`: the part that tells a working OpenRouter setup from
# one that merely looks configured.
# --------------------------------------------------------------------------- #


def _openrouter_cfg(**overrides) -> Config:
    cfg = Config()
    cfg.brain.backend = "openrouter"
    cfg.brain.model = MODEL
    cfg.brain.base_url = "https://openrouter.ai/api/v1"
    cfg.brain.api_key = "sk-or-test"
    for name, value in overrides.items():
        setattr(cfg.brain, name, value)
    return cfg


def _catalogue(ids) -> _Response:
    return _Response(body={"data": [
        {
            "id": model_id,
            "context_length": 1048576,
            "pricing": {"prompt": "0" if model_id.endswith(":free") else "0.000000065"},
            "architecture": {"input_modalities": ["text", "image"] if "vision" in model_id else ["text"]},
        }
        for model_id in ids
    ]})


def test_check_accepts_a_working_free_model(capsys, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    cfg = _openrouter_cfg(use_vision=False)
    key = _Response(body={"data": {"is_free_tier": True, "usage": 0, "limit": 0}})

    with patch("requests.get", side_effect=[key, _catalogue([MODEL])]) as get:
        run._check_openrouter(cfg)

    out = _plain(capsys.readouterr().out)
    assert "OpenRouter key accepted (free tier)" in out
    assert f"model '{MODEL}' is available" in out
    assert "inputs: text, free" in out
    assert get.call_args_list[0].args[0] == "https://openrouter.ai/api/v1/key"


def test_check_warns_when_a_text_only_model_is_asked_for_screenshots(capsys, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    cfg = _openrouter_cfg(use_vision=True)

    with patch("requests.get", side_effect=[
        _Response(body={"data": {"is_free_tier": True}}),
        _catalogue([MODEL]),
    ]):
        run._check_openrouter(cfg)

    out = _plain(capsys.readouterr().out)
    assert "does not accept images" in out
    assert "JARVIS_VISION=0" in out


def test_check_rejects_an_invalid_key_without_asking_for_models(capsys, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with patch("requests.get", return_value=_Response(status_code=401)) as get:
        run._check_openrouter(_openrouter_cfg())

    assert "rejected the key (401)" in _plain(capsys.readouterr().out)
    assert get.call_count == 1


def test_check_names_a_retired_model_slug(capsys, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with patch("requests.get", side_effect=[
        _Response(body={"data": {"is_free_tier": True}}),
        _catalogue(["deepseek/deepseek-v4-flash-vision-exp", "deepseek/deepseek-v4-flash-0731:batch"]),
    ]):
        run._check_openrouter(_openrouter_cfg())

    out = _plain(capsys.readouterr().out)
    assert "is not in OpenRouter's catalogue" in out
    assert "similar: deepseek/deepseek-v4-flash-0731:batch" in out  # the near miss is offered


def test_check_says_how_to_add_a_missing_key(capsys, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with patch("requests.get") as get:
        run._check_openrouter(_openrouter_cfg(api_key=""))

    assert not get.called
    assert "OPENROUTER_API_KEY" in _plain(capsys.readouterr().out)


def test_check_survives_an_unreachable_openrouter(capsys, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with patch("requests.get", side_effect=OSError("no route to host")):
        run._check_openrouter(_openrouter_cfg())  # must not raise

    assert "OpenRouter unreachable" in _plain(capsys.readouterr().out)
