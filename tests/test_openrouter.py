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
    ProviderBusy,
    VisionState,
    _BUSY_BUDGET,
    complete_with_retry,
    is_provider_busy,
    make_brain,
)
from jarvis.config import BrainConfig, Config, load_config  # noqa: E402
from jarvis.providers import (  # noqa: E402
    _BUSY_MARKS,
    _TRANSIENT_MARKS,
    provider_error_message,
)
from jarvis.utils.logging import friendly_error  # noqa: E402

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


def test_an_unconfigured_openai_compatible_server_falls_back_to_the_default(monkeypatch):
    """A local server with no URL configured still has to send somewhere, and
    that endpoint is the table's answer rather than a literal kept in the brain.

    vLLM is the shape of it: an OpenAI-compatible row that names no endpoint, so
    nothing fills one in and the request has to fall back. With no key anywhere,
    the default is the OpenAI endpoint - a key is what makes it OpenRouter, so
    both sources of one are pinned off rather than left to the machine.
    """
    for name in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "JARVIS_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("jarvis.security.get_secret", lambda *a, **k: "")

    brain = make_brain(BrainConfig(backend="vllm", model="local-model"))
    assert brain.cfg.base_url == ""
    seen: dict = {}

    def fake_post(url, **kwargs):
        seen["url"] = url
        return _ok_response()

    monkeypatch.setattr(brain, "_http_post", fake_post)
    brain.complete("system", [{"role": "user", "content": "hi"}])

    assert seen["url"] == "https://api.openai.com/v1/chat/completions"


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
        # Serialize rather than keep the reference: cheaper to read in a failure,
        # and nothing the fallback does to the body can change it afterwards.
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


# --------------------------------------------------------------------------- #
# A gateway that is out of capacity: 429 / 502 / 503 / 504
# --------------------------------------------------------------------------- #

def _busy(status: int = 429) -> _Response:
    """The answer a gateway gives when the route it needs is exhausted."""
    return _Response(status_code=status, body={"error": {"message":
        "All credentials for model gemini-3.7-flash are cooling down - rate "
        "limited - free models allow only a few requests per minute"}})


def _shot() -> Image.Image:
    return Image.new("RGB", (16, 16), "white")


def _delays(monkeypatch) -> Mock:
    """The retry delays, without the wall-clock wait."""
    sleep = Mock()
    monkeypatch.setattr("jarvis.agent.brain.time.sleep", sleep)
    return sleep


def test_a_busy_gateway_with_a_screenshot_is_retried_as_text():
    """The refusal is about capacity on the route the picture takes, so the one
    thing that unblocks the step is sending the same turn without it."""
    brain = _brain()
    post = Mock(side_effect=[_busy(), _ok_response("done")])
    brain._http_post = post

    assert brain.complete("system", [{"role": "user", "content": "task"}],
                          image=_shot()) == "done"

    first, second = [call.kwargs["json"] for call in post.call_args_list]
    assert "image_url" in json.dumps(first)        # the first attempt really sent it
    assert "image_url" not in json.dumps(second)   # the retry did not


def test_a_busy_gateway_pauses_screenshots_rather_than_disabling_vision():
    """A rate limit clears on its own, so switching vision off for the run would
    throw away a capability that is working again a minute later."""
    brain = _brain()
    post = Mock(side_effect=[_busy(), _ok_response("done"), _ok_response("done")])
    brain._http_post = post

    brain.complete("system", [{"role": "user", "content": "task"}], image=_shot())

    assert brain.cfg.use_vision is True            # not disabled for the run
    assert post.call_count == 2                    # refusal, then the text retry

    brain.complete("system", [{"role": "user", "content": "task"}], image=_shot())

    assert post.call_count == 3                    # no second wasted refusal
    assert "image_url" not in json.dumps(post.call_args_list[2].kwargs["json"])


# --------------------------------------------------------------------------- #
# "Can vision be used right now?" - one answer, from its owner
# --------------------------------------------------------------------------- #

def test_vision_state_reports_the_setting_while_nothing_is_wrong():
    brain = _brain()
    assert brain.vision_state() == VisionState(True, True)

    brain.cfg.use_vision = False
    assert brain.vision_state() == VisionState(False, False)


def test_vision_state_carries_the_pause_and_clears_itself(monkeypatch):
    """The pause used to live only inside the request path, so every caller had
    to re-derive it from a private attribute (and the UI reported the setting
    instead). It is a state now - readable, and self-clearing, because a rate
    limit clears."""
    brain = _brain()
    brain._http_post = Mock(side_effect=[_busy(), _ok_response("done")])
    brain.complete("system", [{"role": "user", "content": "task"}], image=_shot())

    paused = brain.vision_state()
    assert (paused.configured, paused.usable) == (True, False)
    assert "busy" in paused.reason          # plain words: the user reads this
    assert paused.retry_in > 0

    # One pause-length later, without touching the setting: usable again.
    monkeypatch.setattr("jarvis.agent.brain.time.time",
                        lambda: brain._vision_paused_until + 1)
    assert brain.vision_state() == VisionState(True, True)


def test_the_screenshot_comes_back_on_its_own_when_the_pause_expires(monkeypatch):
    """The pause is a pause, not a switch: once it lapses the picture goes out
    again with nothing for the user to do. A latched flag would look identical
    until someone noticed screenshots had stopped for the rest of the session."""
    brain = _brain()
    post = Mock(side_effect=[_busy(), _ok_response("done"), _ok_response("done")])
    brain._http_post = post
    brain.complete("system", [{"role": "user", "content": "task"}], image=_shot())

    assert brain.vision_state().usable is False
    assert "image_url" not in json.dumps(post.call_args_list[-1].kwargs["json"])

    monkeypatch.setattr("jarvis.agent.brain.time.time",
                        lambda: brain._vision_paused_until + 1)
    brain.complete("system", [{"role": "user", "content": "task"}], image=_shot())

    assert brain.vision_state().usable is True
    assert "image_url" in json.dumps(post.call_args_list[-1].kwargs["json"])


def test_the_retry_copies_the_request_instead_of_editing_it():
    """What was sent the first time has to survive the retry.

    The first body is still referenced afterwards - by the trajectory writer, by
    an outer retry, by a test - so repairing the request in place hides what the
    gateway actually refused. (The caller's own messages are only a guard: they
    were never aliased, because `_coalesce_roles` copies on the way in.)
    """
    brain = _brain()
    post = Mock(side_effect=[_busy(), _ok_response("done")])
    brain._http_post = post
    messages = [{"role": "user", "content": "task"}]

    brain.complete("system", messages, image=_shot())

    first, second = [call.kwargs["json"] for call in post.call_args_list]
    assert first is not second                     # a new body, not an edited one
    assert "image_url" in json.dumps(first)        # the original body is intact
    assert messages == [{"role": "user", "content": "task"}]


def test_a_busy_turn_without_a_screenshot_is_waited_out_then_answered(monkeypatch):
    """There is no picture to drop on a plain text turn, so waiting is the whole
    remedy - and it has to outlast a per-minute window, not a 2-second blip.
    The task-side profile is stated explicitly: it is the caller's decision, not
    something a new call site inherits by accident."""
    sleep = _delays(monkeypatch)
    brain = Mock()
    brain.complete.side_effect = [BrainError("429 busy", status=429),
                                  BrainError("429 busy", status=429), "done"]

    assert complete_with_retry(brain, "system", [],
                               task_patience=True) == "done"

    assert brain.complete.call_count == 3
    assert [call.args[0] for call in sleep.call_args_list] == [5, 15]


def test_the_task_retry_drops_a_screenshot_the_route_refuses(monkeypatch):
    """The route that fails is the one pictures take - this gateway refused
    every image request during the pass this was written for. Waiting on it
    again and again spends the whole budget on the part that is broken, so the
    retry resends the same turn without the picture: the step still has the
    element list to ground itself in, and the same budget now has a chance."""
    sleep = _delays(monkeypatch)
    brain = Mock()

    def complete(system, messages, image=None):
        if image is not None:
            raise BrainError("429 busy", status=429)     # the picture route
        return "done"                                    # text gets through

    brain.complete.side_effect = complete

    assert complete_with_retry(brain, "system", [], image=_shot(),
                               task_patience=True) == "done"

    first, second = brain.complete.call_args_list
    assert first.kwargs["image"] is not None     # it did try the picture first
    assert second.kwargs["image"] is None        # then stopped paying for it
    assert sleep.call_count == 1                 # one wait, not the whole budget


def test_an_interactive_caller_keeps_the_picture_and_fails_within_seconds(monkeypatch):
    """The default profile is for callers a person is waiting on - the live
    "what am I looking at" path. There the picture IS the question, so dropping
    it would answer a different one, and a ~95-second capacity wait is not
    something to make someone sit through for a question they can re-ask."""
    sleep = _delays(monkeypatch)
    brain = Mock()
    brain.complete.side_effect = ProviderBusy("429 busy", status=429)

    with pytest.raises(ProviderBusy):
        complete_with_retry(brain, "system", [], image=_shot())

    assert brain.complete.call_count == 3            # tries, not the busy budget
    assert all(call.kwargs["image"] is not None
               for call in brain.complete.call_args_list)
    assert sum(call.args[0] for call in sleep.call_args_list) <= 20


def test_the_busy_verdict_is_one_verdict_read_by_both():
    """The retry policy and the sentence the user reads must not disagree.

    They used to: the brain judged "busy" by type, status code and wording,
    while the presentation layer judged by type alone - so a 503 the brain had
    just waited out still reached the user as a generic "something went
    wrong", which is exactly the failure the generic line cannot explain."""
    from jarvis.utils.logging import _provider_is_busy

    shapes = [BrainError("provider rejected model x (503)", status=503),
              BrainError("the gateway is cooling down"),
              RuntimeError("Error 429: rate limit exceeded"),
              ProviderBusy("the AI service answered with nothing at all",
                           silent=True),
              ValueError("exotic failure 0xBEEF")]
    for exc in shapes:
        assert _provider_is_busy(exc) == is_provider_busy(exc)

    said = friendly_error(BrainError("provider rejected model x (503)",
                                     status=503))
    assert "something went wrong" not in said.lower()
    assert "minute" in said.lower()                  # and a way forward


def test_a_200_with_no_text_is_retried_like_a_busy_route(monkeypatch):
    """A gateway can answer *200 with an empty body* - observed on OmniRoute's
    auto routing. Handed to the loop as a string it reads as "the model said
    something invalid", which burns the off-script budget and abandons the plan;
    it is the same silence a busy status describes, so it waits and asks again.
    """
    sleep = _delays(monkeypatch)
    brain = _brain()
    brain._http_post = Mock(side_effect=[_ok_response(""),
                                         _ok_response('{"action": "finish"}')])

    assert complete_with_retry(brain, "system", [],
                               task_patience=True) == '{"action": "finish"}'
    assert brain._http_post.call_count == 2          # the silence cost one retry
    assert [call.args[0] for call in sleep.call_args_list] == [5]


def test_a_blank_reply_that_never_clears_gives_up_on_the_busy_budget(monkeypatch):
    """Silence that does not clear is still a bounded, honest wait - and the
    error names the shape, so the user-facing translation can say "it went
    quiet" rather than blaming the model's reply."""
    sleep = _delays(monkeypatch)
    brain = Mock()
    brain.complete.return_value = ""                 # every attempt comes back empty

    with pytest.raises(ProviderBusy) as excinfo:
        complete_with_retry(brain, "system", [], task_patience=True)

    assert brain.complete.call_count == _BUSY_BUDGET
    delays = [call.args[0] for call in sleep.call_args_list]
    assert len(delays) == _BUSY_BUDGET - 1
    assert 60 <= sum(delays) <= 120                  # long, but still a bound
    assert "ask again" in str(excinfo.value).lower()
    assert excinfo.value.silent is True


def test_the_user_is_told_plainly_when_the_route_keeps_going_quiet(monkeypatch):
    """The technical detail belongs in the log; the line the user reads has to
    say what happened and what to do. A blank reply never reaches
    ``friendly_error`` as anything but a nameable situation."""
    _delays(monkeypatch)
    brain = Mock()
    brain.complete.return_value = ""

    with pytest.raises(BrainError) as excinfo:
        complete_with_retry(brain, "system", [], task_patience=True)

    said = friendly_error(excinfo.value)
    assert "quiet" in said.lower() or "empty" in said.lower()
    assert "ask me again" in said.lower()
    assert "something went wrong" not in said.lower()


def test_a_prose_reply_is_not_treated_as_silence(monkeypatch):
    """The boundary. Only *empty* is silence; a model that answers in prose has
    answered, badly, and the loop's own pushback is what corrects that.
    Retrying it as busy would spend 95s on a mistake the model can fix on the
    next turn."""
    sleep = _delays(monkeypatch)
    brain = Mock()
    brain.complete.return_value = "Sure! I'll open the browser for you."

    assert complete_with_retry(brain, "system", []) == \
        "Sure! I'll open the browser for you."
    assert brain.complete.call_count == 1
    sleep.assert_not_called()


def test_a_busy_status_is_read_from_the_status_and_not_the_wording(monkeypatch):
    """A busy reply is only retried by luck when its wording is what decides.

    This one arrives with neither the word "timeout" nor a status code in its
    text - the way another backend or a transport failure would - so it can only
    be classified from the status it carries. Classified from wording instead, it
    takes the short unknown-error budget and the task dies on the spot.
    """
    quiet = "the gateway's queue gave up waiting for a slot"
    assert not any(mark in quiet for mark in _TRANSIENT_MARKS)
    assert not any(mark in quiet for mark in _BUSY_MARKS)

    sleep = _delays(monkeypatch)
    brain = Mock()
    brain.complete.side_effect = BrainError(quiet, status=504)

    with pytest.raises(BrainError) as excinfo:
        complete_with_retry(brain, "system", [], task_patience=True)

    assert brain.complete.call_count == _BUSY_BUDGET        # the busy budget, from the status
    delays = [call.args[0] for call in sleep.call_args_list]
    assert len(delays) == _BUSY_BUDGET - 1
    assert 60 <= sum(delays) <= 120                         # long, but still a bound
    assert "ask again" in str(excinfo.value).lower()        # and a way forward for the user


def test_a_busy_turn_with_nothing_to_strip_reports_the_error():
    """Nothing to drop means nothing to fall back to: the failure reaches the
    caller with the provider's own words, and carrying the status that the retry
    policy reads."""
    brain = _brain()
    brain._http_post = Mock(return_value=_busy(503))

    with pytest.raises(BrainError) as excinfo:
        brain.complete("system", [{"role": "user", "content": "task"}])

    assert "cooling down" in str(excinfo.value)
    assert excinfo.value.status == 503


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
    # Blank rather than deleted: load_config calls load_dotenv on every run and
    # that only skips names already present in the environment, so a deleted
    # name is re-populated from the project's .env. An empty string is falsy
    # (the override is skipped, as this test intends) and stays empty.
    for name in ("JARVIS_BASE_URL", "BASE_URL", "JARVIS_API_KEY", "API_KEY"):
        monkeypatch.setenv(name, "")

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
