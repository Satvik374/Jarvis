"""OmniRoute provider wiring: one gateway, one owner for its key.

OmniRoute is a local, OpenAI-compatible AI gateway (http://localhost:20128/v1)
that mints its own `sk-` keys. These gates exist because two of the app's
existing shortcuts actively fight that, and either one silently points the brain
at a different provider: `load_config` prefers a stored OpenRouter key over any
other, and `make_brain` treats an `sk-or-` shaped key as proof the backend is
OpenRouter. Each is pinned below so a later edit cannot undo the routing.

Behaviour the gateway shares with every OpenAI-compatible endpoint - how a busy
response is absorbed, what happens to a screenshot the provider will not take -
is gated in test_openrouter.py, which owns the generic transport, rather than
being covered a second time here.

The last section widens from this one gateway to the table itself
(jarvis/providers.py): the endpoint and key variable that used to live in two
files at once are read from one row, and "is it reachable?" is one step.
"""
from __future__ import annotations

from pathlib import Path
import re
import sys
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import run  # noqa: E402
from jarvis import console, providers  # noqa: E402
from jarvis.agent.brain import BrainError, OpenAICompatBrain, make_brain  # noqa: E402
from jarvis.config import BrainConfig, Config, load_config  # noqa: E402
from jarvis.providers import apply_defaults  # noqa: E402

GATEWAY = "http://localhost:20128/v1"


def _isolate(monkeypatch, **env: str) -> None:
    """Set every name ``load_config`` reads, so the project's own ``.env``
    cannot decide the outcome.

    ``load_config`` calls ``load_dotenv`` on every invocation, and that file
    pins JARVIS_BACKEND / MODEL_ID for this machine - so a gate that merely
    deleted those names would be re-populated mid-call and assert nothing.
    """
    blank = ("JARVIS_BASE_URL", "BASE_URL", "OMNIROUTE_API_KEY",
             "OPENROUTER_API_KEY", "OPENAI_API_KEY", "JARVIS_API_KEY",
             "API_KEY", "JARVIS_VISION", "BACKEND", "MODEL_ID", "MODEL")
    for key in blank + ("JARVIS_BACKEND", "JARVIS_MODEL"):
        monkeypatch.delenv(key, raising=False)
    # Blank, not absent: load_dotenv(override=False) only skips names already in
    # the environment, so a deleted name is re-populated from the project's own
    # .env mid-call. Empty strings are falsy, so every `if v :=` override skips.
    for key in blank:
        monkeypatch.setenv(key, "")
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def _yaml(tmp_path, text: str):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _cfg() -> Config:
    return Config(brain=BrainConfig(backend="omniroute", model="auto/vision",
                                    base_url=GATEWAY, api_key="sk-omni-test"))


def _plain(output: str) -> str:
    return " ".join(re.sub(r"\x1b\[[0-9;]*m", "", output).split())


def test_the_omniroute_backend_resolves_to_the_local_gateway(tmp_path, monkeypatch):
    _isolate(monkeypatch, JARVIS_BACKEND="omniroute", JARVIS_MODEL="auto/vision",
             OMNIROUTE_API_KEY="sk-omni-test")

    brain = load_config(_yaml(tmp_path, "brain:\n  backend: omniroute\n")).brain

    assert brain.backend == "omniroute"
    assert brain.base_url == GATEWAY
    assert brain.api_key_env == "OMNIROUTE_API_KEY"
    assert brain.model == "auto/vision"


def test_the_gateway_key_outranks_a_stored_openrouter_key(tmp_path, monkeypatch):
    """Sending the OpenRouter key to localhost is the failure mode this
    prevents: it would 401 on every request while the log still reported a key
    as configured."""
    _isolate(monkeypatch, JARVIS_BACKEND="omniroute", JARVIS_MODEL="auto/vision",
             OMNIROUTE_API_KEY="sk-omni-test", OPENROUTER_API_KEY="sk-or-someone-else")

    brain = load_config(_yaml(tmp_path, "brain:\n  backend: omniroute\n")).brain

    assert brain.api_key == "sk-omni-test"


def test_an_explicit_base_url_is_never_overwritten(tmp_path, monkeypatch):
    """A gateway on another port or host stays where the operator put it."""
    _isolate(monkeypatch, JARVIS_BACKEND="omniroute", JARVIS_MODEL="auto/vision",
             JARVIS_BASE_URL="http://192.168.1.9:20128/v1", OMNIROUTE_API_KEY="sk-omni-test")

    brain = load_config(_yaml(tmp_path, "brain:\n  backend: omniroute\n")).brain

    assert brain.base_url == "http://192.168.1.9:20128/v1"


def test_make_brain_points_at_the_gateway_with_its_own_key_name():
    brain = make_brain(BrainConfig(backend="omniroute", model="auto/vision"))

    assert isinstance(brain, OpenAICompatBrain)
    assert brain.cfg.base_url == GATEWAY
    assert brain.cfg.api_key_env == "OMNIROUTE_API_KEY"


def test_an_sk_or_shaped_key_does_not_turn_omniroute_into_openrouter():
    """The `sk-or-` heuristic in make_brain must not reach a gateway key that
    happens to look like one, or the request leaves the machine entirely."""
    brain = make_brain(BrainConfig(backend="omniroute", model="auto/vision",
                                   api_key="sk-or-looks-like-openrouter"))

    assert brain.cfg.base_url == GATEWAY
    assert brain.cfg.api_key_env == "OMNIROUTE_API_KEY"


def test_openrouter_still_defaults_to_its_own_endpoint():
    """The omniroute branch was added ahead of the OpenRouter one; this keeps
    that insertion from swallowing the existing behaviour."""
    brain = make_brain(BrainConfig(backend="openrouter", model="some/model"))

    assert brain.cfg.base_url == "https://openrouter.ai/api/v1"
    assert brain.cfg.api_key_env == "OPENROUTER_API_KEY"


def test_check_survives_a_gateway_that_answers_200_with_a_non_json_body(capsys):
    """A proxy or captive portal answering for the gateway. The OpenRouter
    checker guards this same parse; --check must warn, not traceback."""
    body = Mock(status_code=200, ok=True)
    body.json.side_effect = ValueError("no JSON body")

    with patch("requests.get", return_value=body):
        run._check_omniroute(_cfg())          # must not raise

    assert "body that is not JSON" in _plain(capsys.readouterr().out)


# --------------------------------------------------------------------------- #
# One owner for what a backend name means. Each gate below edits the table and
# watches a reader follow it, which is what a second copy of the value inside
# config.py or brain.py would prevent.
# --------------------------------------------------------------------------- #

def _row_replaced(monkeypatch, label: str, **changes) -> None:
    """Swap one row of the table, and the alias index built from it."""
    rows = tuple(row._replace(**changes) if row.label == label else row
                 for row in providers.PROVIDERS)
    monkeypatch.setattr(providers, "PROVIDERS", rows)
    monkeypatch.setattr(providers, "_BY_ALIAS",
                        {alias: row for row in rows for alias in row.aliases})


def test_the_loader_reads_the_gateway_from_the_table(tmp_path, monkeypatch):
    """The endpoint moves with the row. Held as a copy in config.py instead, the
    loader would still answer localhost and the two would silently diverge."""
    _isolate(monkeypatch, JARVIS_BACKEND="omniroute", JARVIS_MODEL="auto/vision")
    _row_replaced(monkeypatch, "OmniRoute",
                  base_url="http://10.0.0.5:20128/v1", api_key_env="GATEWAY_KEY")

    brain = load_config(_yaml(tmp_path, "brain:\n  backend: omniroute\n")).brain

    assert brain.base_url == "http://10.0.0.5:20128/v1"
    assert brain.api_key_env == "GATEWAY_KEY"


def test_the_brain_factory_reads_the_same_row(monkeypatch):
    """The other half of the pair: make_brain used to carry its own copy."""
    _row_replaced(monkeypatch, "OmniRoute",
                  base_url="http://10.0.0.5:20128/v1", api_key_env="GATEWAY_KEY")

    brain = make_brain(BrainConfig(backend="omniroute", model="auto/vision"))

    assert brain.cfg.base_url == "http://10.0.0.5:20128/v1"
    assert brain.cfg.api_key_env == "GATEWAY_KEY"


@pytest.mark.parametrize("row", providers.PROVIDERS, ids=lambda r: r.label)
def test_every_row_in_the_table_builds_a_brain(row):
    """Adding a provider is one row and nothing else: a row that selects no
    transport is the failure this catches."""
    brain = make_brain(BrainConfig(backend=row.aliases[0], model="m"))

    assert providers.provider_for(row.aliases[0]) is row
    assert brain.cfg.backend == row.aliases[0]
    if row.base_url:
        assert brain.cfg.base_url == row.base_url


def test_a_gateways_key_name_is_its_own_and_a_local_endpoints_is_yours():
    """A gateway mints its own keys, so another variable's name there is a
    mistake that guarantees a 401; a local server has no key to name, so the
    operator's choice stands."""
    gateway = BrainConfig(backend="omniroute", model="m", api_key_env="MY_KEY")
    apply_defaults(gateway)
    assert gateway.api_key_env == "OMNIROUTE_API_KEY"

    local = BrainConfig(backend="openai", model="m", api_key_env="MY_KEY")
    apply_defaults(local)
    assert local.api_key_env == "MY_KEY"


# Which per-kind line --check must print. The kinds missing here (hf, anthropic,
# and the keyless local servers) have no branch, and never had one.
_CHECK_LINE = {
    "gemini": "Gemini",
    "ollama": "Ollama",
    "codex": "Codex",
    "foundry": "Microsoft Foundry Agent configured",
}


@pytest.mark.parametrize("row", providers.PROVIDERS, ids=lambda r: r.label)
def test_every_alias_reaches_the_check_for_its_kind(row, monkeypatch, capsys):
    """Walk every alias instead of trusting a hand-kept set.

    The foundry set was written out in three files and omitted ``gpt-6``, which
    the factory builds happily - so --check printed nothing at all about that
    backend. An alias that reaches no branch is the failure this catches.
    """
    monkeypatch.setattr("jarvis.auth.codex_oauth.load_tokens", lambda: {})
    monkeypatch.setattr("jarvis.auth.azure_auth.check_auth_status",
                        lambda **kw: {"authenticated": False})
    monkeypatch.setattr(providers, "probe", lambda url, **kw: (None, "refused"))

    for alias in row.aliases:
        run.run_check(Config(brain=BrainConfig(backend=alias, model="m",
                                               api_key="sk-test")))
        out = _plain(capsys.readouterr().out)
        assert f"backend = {alias}" in out
        expected = _CHECK_LINE.get(row.kind)
        assert not expected or expected in out, f"{alias} ({row.kind}) fell through --check"
        if row.kind == "openai" and row.api_key_env:
            assert "API key configured" in out


def test_the_aliases_the_sets_forgot_now_report_their_backend(monkeypatch, capsys):
    """The audit's exact finding, pinned: ``--check --backend gpt-6`` used to
    print nothing about the backend while ``azure-foundry`` reported normally."""
    monkeypatch.setattr("jarvis.auth.azure_auth.check_auth_status",
                        lambda **kw: {"authenticated": False})

    for backend in ("gpt-6", "azure-foundry", "azure_foundry"):
        run.run_check(Config(brain=BrainConfig(backend=backend, model="m")))
        out = _plain(capsys.readouterr().out)
        assert "Microsoft Foundry Agent configured" in out, f"{backend} reported nothing"


def test_the_last_resort_endpoint_is_the_tables(monkeypatch):
    """A local server with no URL configured sends to the table's default, so
    editing that row must move the request - otherwise the brain keeps a second
    copy of the endpoint and the two can drift."""
    _isolate(monkeypatch)
    monkeypatch.setattr("jarvis.security.get_secret", lambda *a, **k: "")
    _row_replaced(monkeypatch, "OpenAI", base_url="http://10.0.0.7:9/v1")
    brain = make_brain(BrainConfig(backend="vllm", model="m"))
    sent: list[str] = []

    def record(url, **kwargs):
        sent.append(url)
        raise BrainError("stop before any network")

    monkeypatch.setattr(brain, "_http_post", record)
    with pytest.raises(BrainError):
        brain.complete("system", [{"role": "user", "content": "hi"}])

    assert sent == ["http://10.0.0.7:9/v1/chat/completions"]


def test_every_alias_resolves_through_the_table():
    """One answer for "which provider, which family" - and none for a stranger."""
    for row in providers.PROVIDERS:
        for alias in row.aliases:
            assert providers.provider_for(alias) is row
            assert providers.kind_of(alias) == row.kind
            assert providers.same_provider(alias, row.aliases[0])
            assert providers.label_for(alias) == row.label

    assert providers.provider_for("not-a-backend") is None
    assert providers.kind_of("not-a-backend") == ""
    assert not providers.same_provider("not-a-backend", "openai")


def test_the_gateway_key_variable_does_not_depend_on_the_line_above(tmp_path, monkeypatch):
    """config.py asks the table which variable holds OmniRoute's key instead of
    reading back what the defaults-filling line just wrote; with that fill
    neutralised, the key must still be found - a reorder must not break it."""
    _isolate(monkeypatch, JARVIS_BACKEND="omniroute", JARVIS_MODEL="auto/vision",
             OMNIROUTE_API_KEY="sk-omni-test")
    monkeypatch.setattr("jarvis.config.apply_defaults", lambda *a, **k: None)

    brain = load_config(_yaml(tmp_path, "brain:\n  backend: omniroute\n")).brain

    assert brain.api_key == "sk-omni-test"


def test_one_reachability_step_serves_every_caller(monkeypatch):
    """Four callers used to ask "is it up?" four ways. Each one reaching for
    its own request is exactly what this pins out: a caller that did would
    bypass this fake entirely."""
    asked: list[str] = []

    def fake_probe(url, *, headers=None, timeout=15.0):
        asked.append(url)
        return None, "refused"            # unreachable, so each caller warns and stops

    monkeypatch.setattr(providers, "probe", fake_probe)

    run._check_omniroute(_cfg())
    run._check_openrouter(_cfg())
    run._check_ollama(_cfg())
    for backend in ("ollama", "omniroute"):
        cfg = _cfg()
        cfg.brain.backend = backend
        console._preflight(cfg)

    assert sorted(asked) == sorted([
        f"{GATEWAY}/models",        # run.py --check, OmniRoute
        f"{GATEWAY}/key",           # run.py --check, OpenRouter
        f"{GATEWAY}/api/tags",      # run.py --check, Ollama
        f"{GATEWAY}/api/tags",      # console preflight, Ollama
        f"{GATEWAY}/models",        # console preflight, OmniRoute
    ])
