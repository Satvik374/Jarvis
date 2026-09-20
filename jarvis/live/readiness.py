"""Why live voice will or will not start.

Every dead voice session in this project has ended the same way: a WebSocket
close code, one console line, and no statement of what was actually missing.
The report below answers that directly - for each voice path, whether it can
authenticate right now and the exact command that supplies whatever it lacks.

There are no network calls. This is a diagnosis of *local configuration*, which
is what makes it cheap enough to compute on every page load and safe to run
before the user has pressed anything.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..utils.adc import ADC_RELATIVE_PATHS as adc_relative_paths
from ..utils.adc import adc_path as find_adc_path


#: Kept as a re-export: the search itself now lives in `jarvis.utils.adc`, so
#: this report and the agent's Vertex brain cannot disagree about where the
#: credentials are.
ADC_RELATIVE_PATHS = adc_relative_paths

#: Env vars `config.py` accepts for the Gemini Live API key, in its order.
LIVE_KEY_ENV_VARS = (
    "JARVIS_LIVE_API_KEY",
    "GEMINI_LIVE_API_KEY",
    "GEMINI_API_KEY",
)

#: Vault entries consulted when no env var is set.
LIVE_KEY_VAULT_NAMES = (
    "JARVIS_LIVE_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
)


@dataclass(frozen=True)
class VoicePath:
    """One way live voice could run, and whether it can start as configured."""

    key: str
    label: str
    ready: bool
    detail: str
    remedy: str = ""
    #: True when the path is configured but only the account's balance can
    #: approve it - a state no local setting can fix.
    account_gated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "ready": self.ready,
            "detail": self.detail,
            "remedy": self.remedy,
            "account_gated": self.account_gated,
        }


#: Failures the process has already observed and that cannot be fixed by
#: retrying: a billing state, not a transient fault. Remembered so that (a)
#: readiness stays accurate after the first attempt, and (b) starting live mode
#: again does not spend another API call to learn the same answer.
_REMEMBERED: dict[str, str] = {}


def remember_failure(key: str, reason: str) -> None:
    """Record a permanent failure for ``key`` so it is not retried blindly."""
    if reason:
        _REMEMBERED[key] = str(reason)


def clear_failures(key: str | None = None) -> None:
    """Forget remembered failures - all of them, or just one path."""
    if key is None:
        _REMEMBERED.clear()
    else:
        _REMEMBERED.pop(key, None)


def remembered_failure(key: str) -> str:
    return _REMEMBERED.get(key, "")


def _env_value(env: Mapping[str, str], name: str) -> str:
    return str(env.get(name, "") or "").strip()


def gemini_api_key(
    config: Any | None = None,
    env: Mapping[str, str] | None = None,
    vault: Any | None = None,
) -> str:
    """Resolve the Gemini Live key the same way ``config.py`` does."""
    env = os.environ if env is None else env
    if config is not None:
        configured = str(getattr(getattr(config, "live_voice", None), "api_key", "") or "").strip()
        if configured:
            return configured
    for name in LIVE_KEY_ENV_VARS:
        value = _env_value(env, name)
        if value:
            return value
    if vault is None:
        try:
            from ..security import get_secret

            vault = get_secret
        except Exception:
            return ""
    for name in LIVE_KEY_VAULT_NAMES:
        try:
            value = str(vault(name) or "").strip()
        except Exception:
            continue
        if value:
            return value
    return ""


def adc_path(
    env: Mapping[str, str] | None = None,
    candidates: Iterable[Path] | None = None,
) -> Path | None:
    """The Application Default Credentials file that exists, if any.

    Delegates to :func:`jarvis.utils.adc.adc_path`, which the agent's Vertex
    brain shares - each used to search its own platform paths, so the report
    could promise credentials the brain could not read.
    """
    return find_adc_path(env, candidates)


def describe(
    config: Any | None = None,
    env: Mapping[str, str] | None = None,
    *,
    adc_candidates: Iterable[Path] | None = None,
    vault: Any | None = None,
    local_tts_model: Path | None = None,
) -> list[VoicePath]:
    """Every live-voice path, best first, each with what it still needs.

    ``config`` is a :class:`jarvis.config.Config`; passing ``None`` still
    produces a useful report from the environment alone.
    """
    env = os.environ if env is None else env
    live = getattr(config, "live_voice", None)

    paths: list[VoicePath] = []

    # 1. Fish Audio Agents: the hosted agent owns the whole speech stack, so it
    #    is the richest path when the account can pay for it.
    fish_agent_id = str(
        getattr(live, "fish_agent_id", "")
        or _env_value(env, "JARVIS_FISH_AGENT_ID")
        or _env_value(env, "FISH_AGENT_ID")
        or ""
    ).strip()
    credit_failure = remembered_failure("fish")
    if not fish_agent_id:
        paths.append(
            VoicePath(
                key="fish",
                label="Fish Audio Agents",
                ready=False,
                detail="no agent is configured",
                remedy="set live_voice.fish_agent_id or JARVIS_FISH_AGENT_ID",
            )
        )
    elif credit_failure:
        paths.append(
            VoicePath(
                key="fish",
                label="Fish Audio Agents",
                ready=False,
                detail=credit_failure,
                remedy="top up the Fish Audio account, or use the Gemini paths below",
                account_gated=True,
            )
        )
    else:
        paths.append(
            VoicePath(
                key="fish",
                label="Fish Audio Agents",
                ready=True,
                detail="agent configured; the account still has to have credit",
            )
        )

    # 2. Gemini Live over the Developer API (an AI Studio key). This is the
    #    free-tier path, and the one that does not depend on a hosted agent.
    api_key = gemini_api_key(config, env, vault)
    # A session the API already refused is worth mentioning here: an out-of-quota
    # key and a usable one look identical in a config file, and the refusal is
    # usually the reason the button does nothing.
    gemini_memory = remembered_failure("gemini")
    grounding_memory = remembered_failure("gemini_grounding")
    # The effective model and voice, not the ones in config.yaml: JARVIS_LIVE_MODEL
    # and JARVIS_LIVE_VOICE in .env override the file, so a stale pair there is
    # the usual reason live voice runs a different model than the one configured.
    from . import gemini_live

    effective = gemini_live.RETIRED_MODELS.get(
        str(getattr(live, "model", "") or "").strip(),
        str(getattr(live, "model", "") or "").strip(),
    ) or gemini_live.DEFAULT_MODEL
    voice = str(getattr(live, "voice_name", "") or "") or gemini_live.DEFAULT_VOICE
    if api_key:
        detail = f"a Gemini API key is configured; model {effective}, voice {voice}"
        if gemini_memory:
            detail = f"{detail}; the last session was refused: {gemini_memory}"
        elif grounding_memory:
            detail = f"{detail}; running without Google Search"
        paths.append(
            VoicePath(
                key="gemini_api_key",
                label="Gemini Live (API key)",
                ready=not gemini_memory,
                detail=detail,
                remedy=(
                    "check the plan and billing for this key, or use another live "
                    "model in live_voice.model"
                    if gemini_memory
                    else ""
                ),
                account_gated=bool(gemini_memory),
            )
        )
    else:
        paths.append(
            VoicePath(
                key="gemini_api_key",
                label="Gemini Live (API key)",
                ready=False,
                detail="no Gemini API key is set",
                remedy=(
                    "create a free key at https://aistudio.google.com/apikey and set "
                    "JARVIS_LIVE_API_KEY in .env"
                ),
            )
        )

    # 3. Gemini Live over Vertex AI, which authenticates with gcloud ADC rather
    #    than a key.
    adc = adc_path(env, adc_candidates)
    backend = str(getattr(live, "backend", "") or "").strip().lower()
    if adc is not None:
        detail = f"application default credentials found at {adc}"
        if backend == "gcloud":
            # A `gcloud` backend means the quotas-billed Vertex path is the one
            # live mode will actually use, so a 429 there is expected rather
            # than surprising - worth saying before the user meets one.
            detail += (
                " (live_voice.backend is 'gcloud', so live mode uses this "
                "quotas-billed Vertex path)"
            )
        paths.append(
            VoicePath(
                key="gemini_vertex",
                label="Gemini Live (Vertex gcloud)",
                ready=True,
                detail=detail,
            )
        )
    else:
        paths.append(
            VoicePath(
                key="gemini_vertex",
                label="Gemini Live (Vertex gcloud)",
                ready=False,
                detail="no application default credentials on this machine",
                remedy="run `gcloud auth application-default login`",
            )
        )

    # 4. A self-hosted OpenAI-Realtime-compatible relay. `ws_url` is the only
    #    thing that feeds this path, so an empty value means it cannot run.
    ws_url = str(
        getattr(live, "ws_url", "")
        or _env_value(env, "JARVIS_LIVE_WS_URL")
        or _env_value(env, "JARVIS_REALTIME_URL")
        or ""
    ).strip()
    paths.append(
        VoicePath(
            key="relay",
            label="Self-hosted realtime relay",
            ready=bool(ws_url),
            detail=ws_url or "live_voice.ws_url is empty",
            remedy="" if ws_url else "set live_voice.ws_url to your relay's WebSocket URL",
        )
    )

    # 5. Local speech, which is not a voice *session* but is what keeps Jarvis
    #    audible when every session path is dead.
    # Derived locally rather than imported from `jarvis.browser`: the browser
    # module imports this one, and a root constant is not worth a cycle.
    model = local_tts_model
    if model is None:
        project_root = Path(__file__).resolve().parents[2]
        model = project_root / "models" / "tts" / "kokoro-v1.0.onnx"
    try:
        local_ready = Path(model).is_file()
    except OSError:
        local_ready = False
    paths.append(
        VoicePath(
            key="local",
            label="Local speech (Kokoro)",
            ready=local_ready,
            detail=(
                "offline text-to-speech is available"
                if local_ready
                else "the Kokoro voice model is not present"
            ),
            remedy=(
                ""
                if local_ready
                else "download the Kokoro model into models/tts/ to speak offline"
            ),
        )
    )

    return paths


def conversation_paths(paths: Iterable[VoicePath]) -> list[VoicePath]:
    """The paths that can hold a real conversation (not local TTS only)."""
    return [path for path in paths if path.key != "local"]


def summary(paths: Iterable[VoicePath], provider: str = "") -> dict[str, Any]:
    """The page-facing shape: the list, the engine, and a ready/blocked verdict."""
    listed = list(paths)
    usable = [path.key for path in conversation_paths(listed) if path.ready]
    return {
        "voice_paths": [path.as_dict() for path in listed],
        "voice_ready": bool(usable),
        "voice_ready_paths": usable,
        "provider": provider or resolve_provider(paths=listed),
    }


#: Readiness path key -> the engine a page would run for it.
_PROVIDER_FOR_PATH = {
    "gemini_api_key": "gemini",
    "gemini_vertex": "gemini",
    "fish": "fish",
    "relay": "relay",
}


def resolve_provider(
    config: Any | None = None,
    env: Mapping[str, str] | None = None,
    vault: Any | None = None,
    paths: Iterable[VoicePath] | None = None,
) -> str:
    """Which engine live voice runs, decided in exactly one place.

    An explicit ``live_voice.provider`` wins outright - a user who asks for Fish
    must not be quietly switched to Gemini because a key happens to be present.
    ``auto`` takes the best path the report says is usable, in the report's own
    order. With nothing usable the answer is still gemini, because the page then
    shows what that path is missing rather than failing silently elsewhere.
    """
    live = getattr(config, "live_voice", None)
    configured = str(getattr(live, "provider", "") or "").strip().lower()
    if configured in {"gemini", "fish", "relay"}:
        return configured

    listed = list(paths) if paths is not None else describe(config, env, vault=vault)
    ready = {path.key for path in conversation_paths(listed) if path.ready}
    for key in ("gemini_api_key", "gemini_vertex", "fish", "relay"):
        if key in ready:
            return _PROVIDER_FOR_PATH[key]
    return "gemini"
