"""The Gemini Live session, defined once for every transport that speaks it.

Three places open the same session: the terminal supervisor
(:mod:`jarvis.live.supervisor`), and the loopback relay behind the browser's
live voice button (:class:`jarvis.browser.LiveSocketRelay`). They must agree on
the setup message, the tool declarations and the quirks below, so all of it
lives here rather than in each caller.

What was verified against the live API, not inferred
---------------------------------------------------
Every claim in this module was checked by opening a real session on
2026-09-19 with a configured key, because the Live API's setup message is
strict enough to reject the whole handshake over one misplaced field:

* ``responseModalities`` and ``speechConfig`` belong inside
  ``generationConfig``. At the top level of ``setup`` the server answers
  ``1007 ... Unknown name "responseModalities"`` and no session opens.
* ``mediaResolution`` also belongs to ``generationConfig`` (the SDK exposes it
  on ``LiveConnectConfig``, which is what makes this easy to get wrong).
* ``contextWindowCompression`` is a *setup* field, not generation config.
* Tool parameters use the protobuf enum spelling (``OBJECT``/``STRING``).
* A single still frame in ``realtimeInput.video`` is not perceived - the model
  answered "I cannot see any image". Frames arriving continuously (about one per
  second) are seen, which is why screen sharing is a stream and not a snapshot.
* ``googleSearch`` grounding is **rejected on accounts without grounding
  quota**: the handshake dies with ``1011 ... exceeded your current quota``.
  Since that failure takes the whole session with it, a search tool is offered
  as *try, then degrade* instead of being baked in. The refusal text is generic
  (`quota_refusal`), so the retry - not the message - is what tells grounded and
  ungrounded quota apart.
* The endpoint sends its JSON **in binary frames**, so ``recv()`` returns
  ``bytes``. A reader that only handles ``str`` sees an open socket, an
  acknowledged setup and then nothing - which is how a working session was
  reported as a dead one, and how a browser that cannot ``JSON.parse`` a Blob
  dropped every message the relay forwarded to it.
* A model can accept a session and ignore the microphone entirely: no
  transcript, no turn, no error. ``gemini-3.8-live`` answered a *text* turn on
  such a session in under two seconds while the same audio that
  ``gemini-3.1-flash-live-preview`` transcribed and answered produced nothing
  from it, at 16 kHz, 24 kHz and 48 kHz and on both ``realtimeInput.audio`` and
  ``realtimeInput.mediaChunks``. That is what ``_AUDIO_DEAF`` and the relay's
  silence watchdog exist for.
* ``googleSearch`` grounding and the Live allowance refuse with the *same*
  sentence, and the grounded attempt is refused *after* ``setupComplete`` rather
  than during it - so the retry, not the handshake, is what tells them apart.
* Ephemeral tokens (Google's documented way to talk to Live API from a browser)
  mint fine on this account but the socket rejects every documented transport -
  ``1008 ... unregistered callers`` for ``?access_token=`` on v1alpha and
  v1beta, and for ``Authorization: Token``. The browser therefore speaks to a
  loopback relay that holds the key, not to Google directly.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

#: Live API endpoint. v1beta is the version this key authenticates against.
GEMINI_LIVE_WS = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)

#: Live model names the API no longer serves, mapped to the model this project
#: runs instead. A retired name left in `live_voice.model` or - the usual place,
#: because it outranks the file - in `.env` would otherwise cost a failed
#: handshake and a ``1006`` close before the fallback list is reached, which
#: reads like a network fault rather than a stale setting.
RETIRED_MODELS: dict[str, str] = {
    "gemini-2.0-flash-exp": "gemini-3.8-live",
    "models/gemini-2.0-flash-exp": "models/gemini-3.8-live",
    "gemini-2.0-flash-realtime-exp": "gemini-3.8-live",
}

#: The model the template names, and the voice it picks.
DEFAULT_MODEL = "gemini-3.8-live"
DEFAULT_VOICE = "Algenib"

#: Sample rates the Live API uses: microphone in, spoken audio out.
PCM_IN_RATE = 16000
PCM_OUT_RATE = 24000

#: Media resolution values, as the enum is spelled on the wire.
_MEDIA_RESOLUTIONS = {
    "low": "MEDIA_RESOLUTION_LOW",
    "medium": "MEDIA_RESOLUTION_MEDIUM",
    "high": "MEDIA_RESOLUTION_HIGH",
}

#: Fragments that mean "this account cannot do that right now": an allowance is
#: spent or the feature is not on the plan.
_QUOTA_MARKS = (
    "quota",
    "billing",
    "rate limit",
    "resource_exhausted",
    "not supported for your plan",
    "permission denied",
)


def media_resolution(value: Any) -> str:
    """Normalise a configured resolution to its wire spelling."""
    text = str(value or "").strip().lower().replace("media_resolution_", "")
    return _MEDIA_RESOLUTIONS.get(text, _MEDIA_RESOLUTIONS["medium"])


def grounding_wanted(config: Any) -> bool:
    """Whether the setup should *offer* Google Search grounding.

    ``auto`` and ``on`` both try; ``off`` never does. ``auto`` exists so the
    first failure can be remembered and skipped on the next start instead of
    spending a handshake per session to relearn it.
    """
    mode = str(getattr(config, "google_search", "auto") or "auto").strip().lower()
    return mode not in {"off", "false", "0", "no", "disabled", "none"}


def quota_refusal(reason: str) -> bool:
    """True when a rejected handshake is about an allowance, not credentials.

    The API's refusal is generic - ``1011 ... You exceeded your current quota,
    please check your plan and billing details`` - and it is *identical* for a
    spent Live allowance and for refused Google Search grounding. Nothing in the
    text distinguishes them, so the caller decides: it knows whether a search
    tool was in the payload it sent, and the only way to tell the two apart is
    to send the same setup again without that tool. That retry is what
    :func:`jarvis.browser.LiveSocketRelay` and the terminal client do.
    """
    text = str(reason or "").lower()
    return any(mark in text for mark in _QUOTA_MARKS)


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #

#: schema.py's parameter types, as the protobuf enum names the Live API expects.
_ENUM_TYPES = {
    "str": "STRING",
    "int": "INTEGER",
    "float": "NUMBER",
    "bool": "BOOLEAN",
    "dict": "OBJECT",
    "list": "ARRAY",
}


def _declaration(spec: dict[str, Any]) -> dict[str, Any]:
    """One OpenAI-Realtime function spec, as a Live API ``functionDeclaration``."""
    source = spec.get("parameters") or {}
    properties: dict[str, Any] = {}
    for name, field in (source.get("properties") or {}).items():
        properties[name] = {
            "type": _ENUM_TYPES.get(str(field.get("type", "str")).lower(), "STRING"),
            "description": str(field.get("description", "")),
        }
    parameters: dict[str, Any] = {"type": "OBJECT", "properties": properties}
    required = [str(name) for name in (source.get("required") or [])]
    if required:
        parameters["required"] = required
    return {
        "name": str(spec.get("name", "")),
        "description": str(spec.get("description", "")),
        "parameters": parameters,
    }


def function_declarations() -> list[dict[str, Any]]:
    """Every tool the voice agent may call, in Live API form.

    Built from :func:`jarvis.live.prompts.get_live_voice_tools` so the browser
    relay, the terminal supervisor and the OpenAI-Realtime spelling all offer
    the same hands - including the resolving screen tools that let the voice
    agent click by name instead of guessing pixels.
    """
    from .prompts import get_live_voice_tools

    return [
        _declaration(spec)
        for spec in get_live_voice_tools()
        if spec.get("name")
    ]


def tools(
    *,
    google_search: bool,
    declarations: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """The ``tools`` array for the setup message.

    Grounding goes first: when it is refused the whole handshake fails, and this
    is the entry the caller drops before retrying. ``declarations`` overrides the
    default tool set, so the terminal supervisor can offer its own delegation
    tools while the browser relay offers the page's.
    """
    entries: list[dict[str, Any]] = []
    if google_search:
        entries.append({"googleSearch": {}})
    entries.append({
        "functionDeclarations": function_declarations() if declarations is None else list(declarations),
    })
    return entries


# --------------------------------------------------------------------------- #
# Setup message
# --------------------------------------------------------------------------- #

def build_setup(
    config: Any,
    *,
    model: str,
    system_instruction: str,
    google_search: bool | None = None,
    declarations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The first message of a Live session, exactly as the API accepts it.

    ``model`` is the bare name (``gemini-3.8-live``); a resource path is passed
    through unchanged so the Vertex spelling keeps working.
    """
    if google_search is None:
        google_search = grounding_wanted(config)

    name = str(model or DEFAULT_MODEL).strip()
    resource = name if name.startswith(("models/", "projects/", "publishers/")) else f"models/{name}"

    setup: dict[str, Any] = {
        "model": resource,
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "mediaResolution": media_resolution(getattr(config, "media_resolution", "medium")),
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": str(getattr(config, "voice_name", "") or DEFAULT_VOICE),
                    }
                }
            },
        },
        # Long voice sessions would otherwise run into the model's context limit
        # mid-conversation; the template's values are kept as the defaults.
        "contextWindowCompression": {
            "triggerTokens": int(getattr(config, "context_trigger_tokens", 104857) or 104857),
            "slidingWindow": {
                "targetTokens": int(getattr(config, "context_sliding_tokens", 52428) or 52428),
            },
        },
        # Both transcripts are requested: the terminal and the browser caption
        # show what was heard and what was said, and neither can be recovered
        # from the audio afterwards.
        "inputAudioTranscription": {},
        "outputAudioTranscription": {},
        "systemInstruction": {"parts": [{"text": str(system_instruction or "")}]},
        "tools": tools(google_search=bool(google_search), declarations=declarations),
    }

    if not getattr(config, "context_window_compression", True):
        setup.pop("contextWindowCompression", None)

    if getattr(config, "session_resumption", True):
        # The server disconnects a long session; with resumption enabled the
        # relay can continue the conversation on a fresh socket instead of
        # dropping the user into a dead caption.
        setup["sessionResumption"] = {}

    return {"setup": setup}


def model_candidates(config: Any) -> list[str]:
    """The configured model first, then live models this key is known to open.

    Tried in order, so an unavailable name costs one failed handshake rather
    than the whole feature. The fallbacks were checked against the API: all
    three answer ``setupComplete`` on a free-tier key.
    """
    configured = str(getattr(config, "model", "") or DEFAULT_MODEL).strip()
    candidates = [
        RETIRED_MODELS.get(configured, configured),
        DEFAULT_MODEL,
        "gemini-3.1-flash-live-preview",
        "gemini-2.5-flash-native-audio-latest",
    ]
    return list(dict.fromkeys([name for name in candidates if name]))


# --------------------------------------------------------------------------- #
# Models that answer the handshake but cannot hear
# --------------------------------------------------------------------------- #
#
# A handshake that succeeds is not proof that the model will hear anything. On
# 2026-09-19 ``gemini-3.8-live`` accepted every setup this module builds - and
# the ephemeral-token-free socket - then returned *nothing at all* for audio:
# no ``inputTranscription``, no ``modelTurn``, at 16 kHz, 24 kHz and 48 kHz,
# with ``realtimeInput.audio`` and with ``realtimeInput.mediaChunks``, while the
# identical clip sent to ``gemini-3.1-flash-live-preview`` came back transcribed
# and answered. A *text* turn on that same session answered with audio in under
# two seconds, so the key, the model name, the session and the quota were all
# fine - only audio input was ignored. Since a session that hears nothing looks
# exactly like a user who has not spoken, the failure has to be remembered and
# stepped over rather than retried forever.
#: How many silent sessions a model gets before it stops being tried first. One
#: is not enough to condemn it: a cold start, a slow first turn or a burst of
#: audio with no pause in it can all look like silence for a few seconds - but
#: silence *while another model answered the same audio* is proof, and \
#: :func:`mark_audio_deaf` takes that verdict straight.
_DEAF_STRIKES = 2

#: Where a measured verdict is kept between runs. Which models hear audio is a
#: property of the key and the rollout, not of the process, so learning it once
#: has to outlive the session that learned it: without this, every restart pays
#: the watchdog window again. The file is keyed by a short fingerprint of the
#: API key, so a second key starts with its own clean slate.
_AUDIO_STATE_ENV = "JARVIS_LIVE_AUDIO_STATE"
_AUDIO_STATE_FILE = "live_audio_models.json"

_AUDIO_DEAF: dict[str, str] = {}
_AUDIO_SILENT_SEEN: dict[str, int] = {}
_AUDIO_STATE: dict[str, dict[str, dict[str, Any]]] = {}
_AUDIO_STATE_LOADED = False


def audio_state_path() -> Path:
    """Where the verdict lives: ``~/.jarvis/live_audio_models.json``."""
    override = os.environ.get(_AUDIO_STATE_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".jarvis" / _AUDIO_STATE_FILE


def bare_model(name: str) -> str:
    """``models/gemini-3.8-live`` and ``gemini-3.8-live`` are the same model.

    Transports spell it both ways - the relay passes the bare name, the terminal
    client keeps the resource path - and a verdict has to match whichever
    spelling asks for it.
    """
    text = str(name or "").strip()
    for prefix in ("models/", "publishers/", "projects/"):
        if text.startswith(prefix):
            return text.rsplit("/", 1)[-1]
    return text


def key_fingerprint(key: str | None) -> str:
    """A short, non-reversible id for an API key, for per-key verdicts."""
    text = str(key or "").strip()
    if not text:
        return "default"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _load_audio_state() -> None:
    global _AUDIO_STATE_LOADED
    if _AUDIO_STATE_LOADED:
        return
    _AUDIO_STATE_LOADED = True
    try:
        raw = audio_state_path().read_text(encoding="utf-8")
        loaded = json.loads(raw)
    except Exception:
        return
    if isinstance(loaded, dict):
        for fingerprint, models in loaded.items():
            if isinstance(models, dict):
                _AUDIO_STATE[str(fingerprint)] = {
                    str(name): dict(record)
                    for name, record in models.items()
                    if isinstance(record, dict)
                }


def _save_audio_state() -> None:
    """Best effort: a read-only home directory must not break live voice."""
    path = audio_state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_AUDIO_STATE, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        return


def _state_for(key: str | None) -> dict[str, dict[str, Any]]:
    _load_audio_state()
    return _AUDIO_STATE.setdefault(key_fingerprint(key), {})


def mark_audio_deaf(
    model: str,
    reason: str = "",
    *,
    key: str | None = None,
    proven: bool = False,
) -> bool:
    """Record a silent session. True once ``model`` has earned a demotion.

    The caller gets told when the strike lands - not on every silent session -
    because only then is it worth telling the user which model was skipped.
    ``proven`` means another model answered the same audio in the same run, so
    this one provably ignores audio on this key and is demoted immediately.
    """
    name = bare_model(model)
    if not name:
        return False
    _AUDIO_SILENT_SEEN[name] = _AUDIO_SILENT_SEEN.get(name, 0) + 1
    record = _state_for(key).setdefault(name, {})
    record["silent_sessions"] = int(record.get("silent_sessions", 0)) + 1
    record["reason"] = str(reason or record.get("reason") or "ignored audio input")
    if not proven and _AUDIO_SILENT_SEEN[name] < _DEAF_STRIKES:
        _save_audio_state()
        return False
    record["deaf"] = True
    _AUDIO_DEAF[name] = str(record["reason"])
    _save_audio_state()
    return True


def audio_deaf(model: str, key: str | None = None) -> bool:
    """True when ``model`` is known to accept a session and ignore audio."""
    name = bare_model(model)
    if not name:
        return False
    if _AUDIO_DEAF.get(name):
        return True
    record = _state_for(key).get(name) or {}
    return bool(record.get("deaf"))


def audio_deaf_reason(model: str, key: str | None = None) -> str:
    name = bare_model(model)
    if _AUDIO_DEAF.get(name):
        return _AUDIO_DEAF[name]
    record = _state_for(key).get(name) or {}
    return str(record.get("reason", ""))


def clear_audio_deaf(key: str | None = None) -> None:
    """Forget the models that ignored audio, so they are tried again.

    With no ``key`` every verdict is dropped, including the ones on disk.
    """
    global _AUDIO_STATE_LOADED
    _AUDIO_DEAF.clear()
    _AUDIO_SILENT_SEEN.clear()
    if key is None:
        _load_audio_state()
        _AUDIO_STATE.clear()
        # Re-read on the next call, so relocating the file (a test, a different
        # account) starts from that file rather than this one's memory.
        _AUDIO_STATE_LOADED = False
    else:
        _state_for(key).clear()
    _save_audio_state()


def hearing_candidates(config: Any, key: str | None = None) -> list[str]:
    """Candidates to open in order, with models that ignore audio last.

    A model that is only deaf *here* - a preview rollout, a regional gap - is
    demoted rather than dropped, so it is still tried once everything that
    hears has been refused. ``key`` scopes the verdict to the key that was
    measured, because another key may serve the same model audio perfectly.
    """
    names = model_candidates(config)
    hearing = [name for name in names if not audio_deaf(name, key)]
    deaf = [name for name in names if audio_deaf(name, key)]
    return hearing + deaf


def preferred_model(config: Any, key: str | None = None) -> str:
    """The model a session should open on: the first one that can hear."""
    return hearing_candidates(config, key)[0]
