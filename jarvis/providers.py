"""The single owner of "what is a backend, and is it up?".

A backend name used to be known in four places - the loader, the brain factory,
the startup check and the console's preflight - and each of them carried its own
copy of the provider's endpoint and key variable. OmniRoute's endpoint was
written out in both ``config.py`` and ``brain.py::make_brain``: two copies of one
default, which agree only until someone edits one of them. Adding a provider
meant edits in four files, and nothing anywhere answered "what providers exist".

The table below answers it, and the helpers under it answer the questions that
belong to a provider rather than to a brain: is it reachable, does it serve
this model, does this failure mean "busy", and which environment variable holds
its key. Brain policy - how long to wait, when to drop a screenshot - stays in
the brain; what a provider's own answer *means* lives here.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from .utils import logging as log

# The name a fresh config starts with. A provider's own variable replaces a
# generic default; a name the user chose on purpose is left alone.
_GENERIC_KEY_ENV = "OPENAI_API_KEY"


class Provider(NamedTuple):
    """One way to talk to a model, and what selecting it implies."""

    label: str                      # how the name is spelled where users see it
    kind: str                       # selects the transport in the brain factory
    aliases: tuple[str, ...]        # backend names that select this provider
    base_url: str = ""              # default endpoint, when the user set none
    api_key_env: str = ""           # variable holding this provider's key
    # True when the key can only live in that one variable - the gateways mint
    # their own keys, so their name is stated rather than filled in when blank.
    key_env_is_fixed: bool = False


PROVIDERS: tuple[Provider, ...] = (
    # OpenAI-compatible transports. A hosted service pins its endpoint; a local
    # server (LM Studio, llama.cpp, vLLM) is whatever URL you configured, so its
    # row names none - and when nothing is configured the OpenAI default below
    # is the one that answers, so that literal has an owner too.
    Provider("OpenAI", "openai", ("openai",),
             "https://api.openai.com/v1", "OPENAI_API_KEY"),
    Provider("LM Studio", "openai", ("lmstudio",)),
    Provider("llama.cpp", "openai", ("llamacpp",)),
    Provider("vLLM", "openai", ("vllm",)),
    Provider("OpenRouter", "openai", ("openrouter",),
             "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", True),
    # OmniRoute pools every provider behind one local endpoint
    # (http://localhost:20128/v1) and mints its own ``sk-`` keys, so
    # OMNIROUTE_API_KEY is the only credential that authenticates there: an
    # OpenRouter key is a different provider's, and the compatibility mirror in
    # OPENAI_API_KEY would answer 401 with the log still calling it configured.
    Provider("OmniRoute", "openai", ("omniroute",),
             "http://localhost:20128/v1", "OMNIROUTE_API_KEY", True),
    Provider("Ollama", "ollama", ("ollama",)),
    Provider("Anthropic", "anthropic", ("anthropic",)),
    Provider("Vertex AI", "gemini", ("gemini", "vertex")),
    Provider("Azure Foundry", "foundry", ("foundry", "azure", "azure-foundry",
                                          "azure_foundry", "foundry-agent",
                                          "gpt-6")),
    Provider("Local HuggingFace", "hf", ("hf", "transformers", "local")),
    Provider("OpenAI Codex", "codex", ("codex", "openai-codex", "chatgpt"),
             "https://chatgpt.com/backend-api/codex"),
)

_BY_ALIAS: dict[str, Provider] = {alias: p for p in PROVIDERS for alias in p.aliases}


def provider_for(backend: str) -> Provider | None:
    """The provider a backend name selects, or None when nothing selects it."""
    return _BY_ALIAS.get((backend or "").strip().lower())


def label_for(backend: str) -> str:
    """How to spell a backend name in output (``omniroute`` -> ``OmniRoute``)."""
    provider = provider_for(backend)
    return provider.label if provider else (backend or "").title()


def kind_of(backend: str) -> str:
    """The transport family a backend name belongs to, or "" when nothing owns it.

    Every alias question - which check to run, which per-kind default to apply -
    is answered here, because the hand-kept sets this replaced had already
    drifted from reality: the foundry set in three files omitted ``gpt-6``, so
    ``--check`` printed nothing about a backend the factory happily built.
    """
    provider = provider_for(backend)
    return provider.kind if provider else ""


def same_provider(backend: str, name: str) -> bool:
    """True when a backend name selects the provider ``name``, alias or not.

    Kinds are families - OpenRouter and OmniRoute share the ``openai`` transport
    but have their own checks - so a per-provider branch asks this rather than
    comparing the two names by hand.
    """
    provider = provider_for(name)
    return provider is not None and provider_for(backend) is provider


def apply_defaults(brain, provider: Provider | None = None) -> None:
    """Fill a brain config's endpoint and key variable from the table.

    ``brain`` is anything with ``backend``/``base_url``/``api_key_env``: the
    loaded ``BrainConfig``, and the one the brain factory is handed. A provider
    that names no endpoint is left exactly as configured.
    """
    provider = provider or provider_for(getattr(brain, "backend", ""))
    if provider is None:
        return
    if provider.base_url and not brain.base_url:
        brain.base_url = provider.base_url
    if provider.api_key_env and (
            provider.key_env_is_fixed
            or not brain.api_key_env
            or brain.api_key_env == _GENERIC_KEY_ENV):
        brain.api_key_env = provider.api_key_env


def probe(url: str, *, headers: dict | None = None,
          timeout: float = 15.0) -> tuple[Any, str]:
    """One GET to a provider endpoint: ``(response, why-not)``.

    This is the "is the backend reachable?" step, in one place. It was four -
    each checker and the console's preflight had its own try/except and its own
    idea of what failure looked like. ``response`` is None only when the request
    never completed, and ``why`` then holds the reason to print after the
    caller's own words.
    """
    try:
        import requests

        return requests.get(url, headers=headers or {}, timeout=timeout), ""
    except Exception as exc:
        return None, str(exc)


def report_model(model: str, catalogue: list, *, label: str,
                 tail: str = "") -> bool:
    """Say whether ``model`` is one this provider serves, offering near misses.

    Both OpenAI-compatible checkers had their own copy of this, and the
    near-miss list is the part that earns its keep: a retired slug and a typo
    look identical without it. Returns False (having warned) when it is absent.
    """
    if any(str(m.get("id")) == model for m in catalogue):
        return True
    stem = model.split("/")[-1].split(":")[0]
    close = sorted(str(m.get("id")) for m in catalogue if stem in str(m.get("id")))
    log.warn(f"model '{model}' is not in {label}'s catalogue "
             f"({len(catalogue)} models){tail}")
    if close:
        log.info(f"similar: {', '.join(close[:6])}")
    return False


# --------------------------------------------------------------------------- #
# How a provider answers: "busy", "rejected", or a key in an environment
# variable.
# --------------------------------------------------------------------------- #

# Retryable wording (trajectory analysis: 21 runs, 11%, died on these). The busy
# status codes ride _BUSY_MARKS, so they are not repeated here.
_TRANSIENT_MARKS = ("timed out", "timeout", "max retries", "connection",
                    "temporarily", "overloaded", "exhausted", "recitation",
                    "refresh access token", "empty content")

# "Out of capacity, ask again". Wording widens a wait; it never decides to drop
# a screenshot or to trust a response.
_BUSY_STATUS_CODES = frozenset({429, 502, 503, 504})
_BUSY_MARKS = ("429", "502", "503", "504", "rate limit", "too many requests",
               "cooling down")

# Status -> the one-line remedy worth printing next to the provider's own words.
_PROVIDER_HINTS = {
    401: "the API key is missing or invalid",
    402: "the account has no credit left for this model",
    403: "this key is not allowed to use that model",
    404: "no provider serves this model id (or not for this input type)",
    429: "rate limited - free models allow only a few requests per minute",
    502: "the gateway lost the provider it routed to",
    503: "no provider is serving this model right now",
    504: "the gateway's queue gave up waiting for a slot; retrying usually works",
}


def busy_status(status: Any) -> bool:
    """True when an HTTP status means "out of capacity, ask again shortly"."""
    return isinstance(status, int) and status in _BUSY_STATUS_CODES


def is_busy_error(exc: BaseException) -> bool:
    """True when a failure says "busy" in words (the status half is busy_status())."""
    low = str(exc).lower()
    return any(mark in low for mark in _BUSY_MARKS)


def is_transient_error(exc: BaseException) -> bool:
    """True when a failure's wording is retryable but is not about capacity."""
    low = str(exc).lower()
    return any(mark in low for mark in _TRANSIENT_MARKS)


# Fragments that mean "this model cannot take an image": a text-only model
# answers a vision request with "No endpoints found that support image input",
# and losing a whole task to a 404 is a bad way to learn that.
_NO_VISION_MARKS = ("image", "modalit", "multimodal", "vision")


def says_no_images(text: str) -> bool:
    """True when a refusal is about the input type rather than about capacity."""
    low = (text or "").lower()
    return any(mark in low for mark in _NO_VISION_MARKS) or "filter by image support" in low


def image_refusal(response: Any) -> str:
    """"congested" (capacity), "rejected" (the model takes no images), or "".

    Capacity comes first: reading a busy status as "no images" would switch
    vision off for the rest of a run.
    """
    if busy_status(getattr(response, "status_code", 0)):
        return "congested"
    return "rejected" if says_no_images(provider_error_message(response)) else ""


def default_base_url(backend: str, key: str) -> str:
    """The endpoint to use when the config names none: OpenRouter's key shape or
    backend selects its endpoint, anything else the OpenAI-compatible default."""
    if same_provider(backend, "openrouter") or (key and key.startswith("sk-or-")):
        return provider_for("openrouter").base_url
    return provider_for("openai").base_url


def attribution_headers(backend: str, base_url: str, key: str) -> dict:
    """The headers OpenRouter asks for; every other provider gets none."""
    if (same_provider(backend, "openrouter") or "openrouter.ai" in (base_url or "")
            or (key and key.startswith("sk-or-"))):
        return {"HTTP-Referer": "https://github.com/jarvis-agent",
                "X-Title": "Jarvis Desktop Assistant"}
    return {}


def provider_error_message(response: Any) -> str:
    """The provider's own error text, not requests' generic status line.

    OpenRouter answers with ``{"error": {"message": ...}}``. ``raise_for_status``
    reports "404 Client Error ... for url", which cannot tell a retired model
    slug from a model that simply takes no images - the two mistakes a new model
    id actually makes.
    """
    try:
        body = response.json()
    except Exception:
        body = None
    detail = ""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            detail = str(err.get("message") or err.get("type") or "")
        elif err:
            detail = str(err)
        detail = detail or str(body.get("message") or body.get("detail") or "")
    if not detail:
        detail = str(getattr(response, "text", "") or "").strip()[:400]
    return detail


def provider_error(url: str, response: Any, model: str) -> str:
    """A BrainError message for a request the provider rejected."""
    status = getattr(response, "status_code", "?")
    detail = provider_error_message(response)
    message = f"provider rejected model '{model}' ({status}) at {url}"
    if detail:
        message += f": {detail}"
    hint = _PROVIDER_HINTS.get(status)
    if hint:
        message += f" - {hint}"
    return message


def env_api_key(backend: str, base_url: str, api_key_env: str) -> str:
    """The API key this backend's environment actually provides.

    Order matters, and it is not the order the variables are named in:
    ``load_config`` mirrors a resolved OpenRouter key into ``OPENAI_API_KEY``
    (a compatibility shim for the older code paths), so for an OpenRouter
    backend the generic OpenAI name has to be consulted *last*. Otherwise a
    stale mirrored key silently shadows the ``OPENROUTER_API_KEY`` the user just
    set, and every request goes out with credentials they did not choose -
    which reads as "my new key does not work".
    """
    import os

    backend = str(backend or "").lower()
    base_url = str(base_url or "")
    # The hostname check is about a URL the operator typed, not a name.
    openrouter = same_provider(backend, "openrouter") or "openrouter.ai" in base_url
    configured = str(api_key_env or "").strip()
    names: list[str] = []
    if openrouter:
        names.append("OPENROUTER_API_KEY")
        # A name the operator chose for *this* backend still comes first; the
        # generic default does not, because the mirror writes into it.
        if configured and configured != _GENERIC_KEY_ENV:
            names.append(configured)
    else:
        names.append(configured or _GENERIC_KEY_ENV)
    names += ["OPENROUTER_API_KEY", "OPENAI_API_KEY"]
    seen: list[str] = []
    for name in names:
        if name and name not in seen:
            seen.append(name)
    return next((os.environ.get(name, "") for name in seen if os.environ.get(name)), "")
