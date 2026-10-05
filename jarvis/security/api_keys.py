"""The ``.env`` credentials, described and edited as data.

Editing ``.env`` by hand is the only way Jarvis has ever accepted an API key,
and it is a bad interface: the file is long, the variable names are not the
product names, and every one of them has to be spelled exactly or the feature
that reads it silently stays off. This module is the one place that knows which
variables exist and what each one is for, so the browser UI can offer them as a
form instead of as text.

Three things are deliberate:

* **The file stays the source of truth.** Everything Jarvis reads is either
  ``os.environ`` (populated from ``.env`` by ``load_dotenv``) or the credential
  vault, which itself falls back to ``os.environ``. A value written here is
  written to the file, so no call site has to learn a new lookup.
* **Writing preserves the file.** Lines are rewritten in place, comments and
  ordering survive, and a key that is already documented in the file keeps its
  line instead of being appended a second time. The newline style is kept too
  (this checkout's ``.env`` is CRLF).
* **A secret is never handed back.** A configured value is reported masked, the
  same way ``:secret get`` reports it, because the page is served over loopback
  and a page that can read a key can leak one. The page can write a value and
  clear one, which is what editing needs.

The one convenience beyond the file is the vault mirror. ``get_secret`` probes
the vault before ``os.environ``, and the agent's real work happens in a child
process that has already loaded ``.env`` by the time the page is up - so a value
written only to the file would sit unused until the next restart. Mirroring it
into the DPAPI vault is what makes a key saved in the browser take effect in the
running session. Credential Manager is deliberately *not* written: that store is
machine-global, and the vault file is repo-local state.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..utils import logging as log
from ..utils import paths as jarvis_paths
from ..utils.paths import project_root

#: An environment variable name, and the limits the UI is held to.
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAX_KEY_LENGTH = 64
MAX_VALUE_LENGTH = 8192
MAX_UPDATES = 64

#: A value that needs no quoting when written. Anything else is double-quoted,
#: which python-dotenv and Jarvis's own fallback parser both strip.
_SAFE_VALUE_RE = re.compile(r"^[A-Za-z0-9_.:/@%+,=\-]*$")

#: ``KEY=value`` with an optional ``export`` prefix and an optional trailing
#: comment, which is rewritten in place rather than dropped.
_ASSIGNMENT_RE = re.compile(
    r"^(?P<prefix>\s*(?:export\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"\s*=\s*)(?P<value>.*?)(?P<trailer>\s*\#.*)?$"
)

#: Where keys the UI adds are parked, so the file explains where they came from.
_ADDED_HEADER = "# --- Added from the Jarvis interface ---"


@dataclass(frozen=True)
class KeySpec:
    """One variable Jarvis reads, and how to describe it to a human."""

    name: str
    label: str
    group: str
    help: str = ""
    url: str = ""
    #: False only for plain settings (a switch, a model name). Everything that
    #: grants access to an account is masked on the way out.
    secret: bool = True


BRAIN = "Brain & gateways"
VOICE = "Voice & speech"
CONNECTORS = "Connectors"
SERVICES = "Other services"
DISCOVERED = "Found in .env"
VAULT_ONLY = "Vault only"

#: Every variable this project reads. The names come from the code that reads
#: them (``jarvis/config.py``, ``jarvis/utils/voice.py``, ``jarvis/live/`` and
#: the connectors), not from a wish list - a name that nothing reads would be a
#: row in the UI that saves a key and changes nothing.
CATALOGUE: Tuple[KeySpec, ...] = (
    # -- Brain -------------------------------------------------------------
    KeySpec(
        "OMNIROUTE_API_KEY", "OmniRoute API key", BRAIN,
        "The default gateway (brain.api_key_env in config.yaml). Its keys are "
        "minted by OmniRoute itself, so they are not interchangeable with an "
        "OpenRouter key.",
    ),
    KeySpec(
        "OPENROUTER_API_KEY", "OpenRouter API key", BRAIN,
        "Used when brain.backend is openrouter.", url="https://openrouter.ai/keys",
    ),
    KeySpec(
        "GEMINI_API_KEY", "Google AI Studio key", BRAIN,
        "Also used by live voice when JARVIS_LIVE_API_KEY is unset.",
        url="https://aistudio.google.com/apikey",
    ),
    KeySpec(
        "OPENAI_API_KEY", "OpenAI API key", BRAIN, url="https://platform.openai.com/api-keys",
    ),
    KeySpec(
        "ANTHROPIC_API_KEY", "Anthropic API key", BRAIN, url="https://console.anthropic.com/settings/keys",
    ),
    KeySpec(
        "JARVIS_API_KEY", "Generic backend key", BRAIN,
        "Fallback for a custom backend that has no variable of its own.",
    ),
    KeySpec(
        "AZURE_API_KEY", "Azure / Foundry key", BRAIN,
        "Read as AZURE_API_KEY or AZURE_AI_KEY for the foundry backend.",
    ),
    KeySpec("BASE_URL", "Backend base URL", BRAIN, "Overrides brain.base_url.", secret=False),
    KeySpec(
        "BACKEND", "Backend name", BRAIN,
        "Which model thinks: omniroute, openrouter, gemini, openai, anthropic, ollama.",
        secret=False,
    ),
    KeySpec("MODEL_ID", "Model id", BRAIN, "Overrides brain.model.", secret=False),
    KeySpec(
        "JARVIS_VISION", "Send screenshots (1/0)", BRAIN,
        "Only worth enabling for a model that accepts images.", secret=False,
    ),
    KeySpec("JARVIS_LOCATION", "Location", BRAIN, "Used for time and weather.", secret=False),
    KeySpec("HF_TOKEN", "Hugging Face token", BRAIN, url="https://huggingface.co/settings/tokens"),

    # -- Voice -------------------------------------------------------------
    KeySpec(
        "JARVIS_LIVE_API_KEY", "Live voice key", VOICE,
        "The live voice engine. A Gemini key goes here; the console's :live "
        "command reports which engine is actually in use.",
        url="https://aistudio.google.com/apikey",
    ),
    KeySpec(
        "FISH_AUDIO_API_KEY", "Fish Audio key", VOICE,
        "Needed when voice.engine is fish.", url="https://fish.audio/go-api/",
    ),
    KeySpec("FISH_AUDIO_VOICE_ID", "Fish Audio voice id", VOICE, secret=False),
    KeySpec(
        "AZURE_SPEECH_KEY", "Azure speech key", VOICE,
        "Only for the azure speech engine.",
    ),
    KeySpec("AZURE_SPEECH_ENDPOINT", "Azure speech endpoint", VOICE, secret=False),
    KeySpec("JARVIS_TTS_ENGINE", "Speech engine", VOICE, "kokoro, azure, fish or openai.", secret=False),
    KeySpec(
        "JARVIS_LIVE_MODEL", "Live model", VOICE,
        "Overrides live_voice.model.", secret=False,
    ),
    KeySpec("JARVIS_LIVE_VOICE", "Live voice name", VOICE, secret=False),

    # -- Connectors --------------------------------------------------------
    KeySpec(
        "GMAIL_ADDRESS", "Gmail address", CONNECTORS,
        "Read over IMAP. The app password, not the account password.",
    ),
    KeySpec(
        "GMAIL_APP_PASSWORD", "Gmail app password", CONNECTORS,
        "16 characters from myaccount.google.com/apppasswords.",
        url="https://myaccount.google.com/apppasswords",
    ),
    KeySpec(
        "DISCORD_BOT_TOKEN", "Discord bot token", CONNECTORS,
        "discord.com/developers -> your app -> Bot. The bot needs the Message "
        "Content intent on, or message text arrives empty.",
        url="https://discord.com/developers/applications",
    ),
    KeySpec("DISCORD_ALLOWED_USERS", "Discord allowed users", CONNECTORS,
            "Comma-separated ids. Blank means only the bot's owner.", secret=True),
    KeySpec("WHATSAPP_TOKEN", "WhatsApp token", CONNECTORS,
            "Meta Cloud API. Test tokens expire after 24 hours."),
    KeySpec("WHATSAPP_PHONE_ID", "WhatsApp phone id", CONNECTORS),
    KeySpec("JARVIS_REMOTE_URL", "Remote relay URL", CONNECTORS,
            "Public HTTPS URL of your relay_server deployment.", secret=False),

    # -- Other services ----------------------------------------------------
    KeySpec("GROQ_API_KEY", "Groq API key", SERVICES, url="https://console.groq.com/keys"),
    KeySpec("PERPLEXITY_API_KEY", "Perplexity API key", SERVICES),
    KeySpec("DEEPSEEK_API_KEY", "DeepSeek API key", SERVICES),
    KeySpec("MISTRAL_API_KEY", "Mistral API key", SERVICES),
    KeySpec("GITHUB_TOKEN", "GitHub token", SERVICES,
            url="https://github.com/settings/tokens"),
    KeySpec("NOTION_API_KEY", "Notion API key", SERVICES),
    KeySpec("SLACK_BOT_TOKEN", "Slack bot token", SERVICES),
    KeySpec("TELEGRAM_BOT_TOKEN", "Telegram bot token", SERVICES),
    KeySpec("LINEAR_API_KEY", "Linear API key", SERVICES),
    KeySpec("WEATHER_API_KEY", "Weather API key", SERVICES),
    KeySpec("SPOTIFY_CLIENT_ID", "Spotify client id", SERVICES),
    KeySpec("SPOTIFY_CLIENT_SECRET", "Spotify client secret", SERVICES),
)

#: name -> spec, for the two lookups below.
SPECS: Dict[str, KeySpec] = {spec.name: spec for spec in CATALOGUE}


# --------------------------------------------------------------------------- #
#  Reading and writing the file
# --------------------------------------------------------------------------- #


def env_path() -> Path:
    """Where the credentials live.

    ``.env`` is an *input* location: it keeps pointing at the installed program
    when state is relocated (see ``jarvis/utils/paths.py``), so this is the
    project root rather than ``state_root()``.
    """
    return project_root() / ".env"


def _same_file(path: Path, other: Path) -> bool:
    try:
        return path.resolve() == other.resolve()
    except OSError:  # pragma: no cover - a path the OS will not resolve
        return str(path) == str(other)


def _read(path: Path) -> Tuple[List[str], str]:
    """The file's lines and the newline style to write them back with."""
    if not path.exists():
        return [], "\n"
    try:
        raw = path.read_bytes()
    except OSError as exc:
        log.warn(f"Could not read {path}: {exc}")
        return [], "\n"
    text = raw.decode("utf-8", errors="replace")
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n").split("\n"), newline


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        inner = value[1:-1]
        return inner.replace('\\"', '"').replace("\\\\", "\\")
    return value


def _quote(value: str) -> str:
    if _SAFE_VALUE_RE.match(value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def parse(lines: Iterable[str]) -> Dict[str, str]:
    """The values assigned in these lines. Later assignments win, as in dotenv."""
    values: Dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ASSIGNMENT_RE.match(line)
        if not match:
            continue
        values[match.group("name")] = _unquote(match.group("value"))
    return values


def _validate(updates: Dict[str, str], remove: Sequence[str]) -> Optional[str]:
    """Return the reason a request is refused, or None when it is acceptable."""
    if len(updates) + len(remove) > MAX_UPDATES:
        return f"too many keys in one request (limit {MAX_UPDATES})"
    for name in list(updates) + list(remove):
        if not isinstance(name, str) or not _NAME_RE.match(name):
            return f"'{name}' is not a valid variable name"
        if len(name) > MAX_KEY_LENGTH:
            return f"'{name}' is longer than {MAX_KEY_LENGTH} characters"
    for name, value in updates.items():
        if not isinstance(value, str):
            return f"the value of '{name}' must be text"
        if len(value) > MAX_VALUE_LENGTH:
            return f"the value of '{name}' is longer than {MAX_VALUE_LENGTH} characters"
        if "\n" in value or "\r" in value:
            return f"the value of '{name}' cannot contain a line break"
    return None


def _restricted_to_project(path: Path) -> bool:
    """True when this process must not write ``path``.

    The suite redirects every state location into a temporary tree, but ``.env``
    is not one of them - it deliberately keeps pointing at the repository. A
    test that drives a write with the default path is therefore addressed at the
    developer's real credentials, which is why the sandbox refuses it. Tests
    that want to exercise a write pass their own path.
    """
    if jarvis_paths.sandbox_root() is None:
        return False
    # Compared against the real location rather than against ``env_path()``:
    # the point is to catch the developer's own credentials, and a test that
    # redirects ``env_path`` at a temporary file must not look like the target.
    return _same_file(path, project_root() / ".env")


def _rewrite(
    lines: List[str],
    updates: Dict[str, str],
    remove: Sequence[str],
) -> List[str]:
    """Apply the changes in place, keeping comments, order and untouched lines.

    Only the *last* assignment of a name is rewritten, because that is the one
    that wins: ``dotenv`` lets a later line override an earlier one, so a file
    that assigns a key twice has a live line and a dead one. Rewriting both
    would silently change what the dead line said - and this file is one the
    user reads, where a diff of two lines for one edit is a bug report. A
    ``remove``, by contrast, drops every line for the name: the point of it is
    that the variable is gone.
    """
    removed = set(remove)
    assignments: Dict[str, int] = {}
    for index, line in enumerate(lines):
        match = _ASSIGNMENT_RE.match(line)
        if match:
            assignments[match.group("name")] = index

    live = {
        name: assignments[name] for name in updates if name in assignments
    }
    pending = {name: value for name, value in updates.items() if name not in live}

    result: List[str] = []
    for index, line in enumerate(lines):
        match = _ASSIGNMENT_RE.match(line)
        name = match.group("name") if match else ""
        if name and name in removed:
            continue
        if match and live.get(name) == index:
            trailer = match.group("trailer") or ""
            value = _quote(updates[name])
            result.append(f"{match.group('prefix')}{value}{trailer}")
            continue
        result.append(line)
    if not pending:
        return result

    # Keys that were not in the file already: appended under one header, so the
    # file itself says which lines the interface added.
    while result and not result[-1].strip():
        result.pop()
    if _ADDED_HEADER not in lines:
        result.extend(["", _ADDED_HEADER])
    result.extend(f"{name}={_quote(value)}" for name, value in pending.items())
    result.append("")
    return result


def _write(path: Path, lines: List[str], newline: str) -> None:
    """Replace the file atomically, so a crash cannot leave it half-written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = newline.join(lines)
    temporary: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, delete=False, prefix=".env-", suffix=".tmp"
        ) as handle:
            temporary = Path(handle.name)
            handle.write(body.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise RuntimeError(f"could not write {path}: {exc}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


# --------------------------------------------------------------------------- #
#  The vault mirror
# --------------------------------------------------------------------------- #


def _vault():
    """The credential vault, or None when it cannot be used in this process."""
    try:
        from .vault import get_credential_vault

        return get_credential_vault()
    except Exception as exc:  # pragma: no cover - import failure only
        log.warn(f"Credential vault unavailable: {exc}")
        return None


def _mirror(name: str, value: str, vault: Any) -> bool:
    """Store one value in the DPAPI vault. Best effort, and reported as such."""
    if vault is None:
        return False
    try:
        return bool(vault.set_secret(name, value, backend="dpapi"))
    except Exception as exc:
        log.warn(f"Could not mirror '{name}' into the vault: {exc}")
        return False


def _unmirror(name: str, vault: Any) -> bool:
    if vault is None:
        return False
    try:
        return bool(vault.delete_secret(name))
    except Exception as exc:
        log.warn(f"Could not remove '{name}' from the vault: {exc}")
        return False


# --------------------------------------------------------------------------- #
#  The API the browser uses
# --------------------------------------------------------------------------- #


def _entry(
    name: str,
    file_values: Dict[str, str],
    vault: Any,
    spec: Optional[KeySpec] = None,
    vault_names: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """One row: what the variable is, whether it is set, and where it is set.

    The vault is only asked about a name it already reports holding. Every
    question costs a read of the machine's credential store, and a page load
    asks about forty names - so the cheap listing decides, and the exact value
    is fetched for the few that need it.
    """
    in_file = name in file_values and bool(file_values[name])
    file_value = file_values.get(name, "")
    vault_value = ""
    if not in_file and vault is not None and (
        vault_names is None or name.upper() in vault_names
    ):
        try:
            vault_value = vault.get_secret(name) or ""
        except Exception:
            vault_value = ""
    env_value = os.environ.get(name, "")
    value = file_value or vault_value or env_value
    if in_file:
        source = "file"
    elif vault_value:
        source = "vault"
    elif env_value:
        source = "environment"
    else:
        source = ""
    secret = True if spec is None else spec.secret
    entry: Dict[str, Any] = {
        "key": name,
        "label": spec.label if spec else name,
        "group": spec.group if spec else DISCOVERED,
        "help": spec.help if spec else "",
        "url": spec.url if spec else "",
        "secret": secret,
        "set": bool(value),
        "source": source,
        "in_file": name in file_values,
        "custom": spec is None,
    }
    # "masked" is always present so the page has one shape to render: empty for
    # a plain setting or an unset key, the mask for a secret that is set.
    entry["masked"] = ""
    if value:
        if secret:
            entry["masked"] = _mask(value)
            # Length is not the value, and it is what tells a truncated paste
            # from a whole key without ever shipping the key to the page.
            entry["length"] = len(value)
        else:
            entry["value"] = value
    return entry


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "\u2022" * 8
    return f"{value[:4]}\u2022\u2022\u2022\u2022{value[-4:]}"


def _vault_names(vault: Any) -> set:
    """The names the machine's credential stores hold, without their values."""
    if vault is None or not hasattr(vault, "list_secrets"):
        return set()
    try:
        return {
            str(item.get("key", "")).strip().upper()
            for item in vault.list_secrets()
            if str(item.get("key", "")).strip()
        }
    except Exception:
        return set()


def describe(path: Optional[Path] = None, vault: Any = None) -> Dict[str, Any]:
    """Every key the interface should show, in catalogue order then by origin."""
    target = Path(path) if path is not None else env_path()
    lines, _ = _read(target)
    file_values = parse(lines)
    store = _vault() if vault is None else vault
    vault_names = _vault_names(store)

    keys: List[Dict[str, Any]] = []
    for spec in CATALOGUE:
        keys.append(_entry(spec.name, file_values, store, spec, vault_names))
    for name in sorted(name for name in file_values if name not in SPECS):
        keys.append(_entry(name, file_values, store, None, vault_names))
    declared = {name.upper() for name in file_values}
    for name in sorted(vault_names - set(SPECS) - declared):
        entry = _entry(name, file_values, store, None, vault_names)
        # Stored in the vault by ':secret set' rather than by this page: worth
        # showing, since it is exactly the credential the user could not find.
        entry["group"] = VAULT_ONLY
        entry["custom"] = True
        keys.append(entry)

    groups: List[str] = []
    for entry in keys:
        if entry["group"] not in groups:
            groups.append(entry["group"])
    configured = sum(1 for entry in keys if entry["set"])
    return {
        "ok": True,
        "env_path": str(target),
        "exists": target.exists(),
        "groups": groups,
        "keys": keys,
        "configured": configured,
        "total": len(keys),
    }


def apply(
    updates: Optional[Dict[str, str]] = None,
    remove: Optional[Sequence[str]] = None,
    path: Optional[Path] = None,
    vault: Any = None,
) -> Dict[str, Any]:
    """Write values into ``.env``, mirror them into the vault, and report back.

    An empty value clears a key: the assignment stays in the file (it is
    documentation as much as configuration) and the vault copy is dropped. A
    name in ``remove`` loses its line entirely - the way to undo a variable the
    interface itself added.
    """
    target = Path(path) if path is not None else env_path()
    if _restricted_to_project(target):
        return {
            "ok": False,
            "error": (
                "refusing to write the real .env while a test sandbox is active; "
                "pass an explicit path"
            ),
        }

    updates = {str(k).strip(): str(v) for k, v in (updates or {}).items()}
    remove = [str(name).strip() for name in (remove or [])]
    if not updates and not remove:
        return {"ok": False, "error": "nothing to save"}
    problem = _validate(updates, remove)
    if problem:
        return {"ok": False, "error": problem}

    lines, newline = _read(target)
    before = parse(lines)
    trimmed = {name: value.strip() for name, value in updates.items()}
    rewritten = _rewrite(lines, trimmed, remove)
    try:
        _write(target, rewritten, newline)
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc)}

    store = _vault() if vault is None else vault
    saved = sorted(name for name, value in trimmed.items() if value)
    cleared = sorted(name for name, value in trimmed.items() if not value)
    stored: List[str] = []
    removed: List[str] = []
    for name in saved:
        # The running process is told first: load_config has already happened,
        # and everything that reads os.getenv directly should see the new value
        # without waiting for a restart.
        os.environ[name] = trimmed[name]
        if _mirror(name, trimmed[name], store):
            stored.append(name)
    for name in cleared:
        os.environ.pop(name, None)
        _unmirror(name, store)
    for name in remove:
        os.environ.pop(name, None)
        _unmirror(name, store)
        removed.append(name)

    changed = saved + cleared + [name for name in remove]
    log.info(
        f"API keys updated from the interface: {', '.join(changed) or 'nothing'}"
    )
    report = describe(target, store)
    report.update(
        {
            "saved": saved,
            "cleared": cleared,
            "removed": removed,
            "vault_stored": stored,
            "applied_live": bool(stored),
            "previous": {name: bool(before.get(name)) for name in changed},
        }
    )
    return report


def summary(path: Optional[Path] = None, vault: Any = None) -> Dict[str, Any]:
    """The counts a status report prints, without the values."""
    report = describe(path, vault)
    unset = sorted(
        entry["key"] for entry in report["keys"] if not entry["set"] and not entry["custom"]
    )
    return {
        "env_path": report["env_path"],
        "configured": report["configured"],
        "total": report["total"],
        "unset": unset,
    }
