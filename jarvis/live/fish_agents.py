"""Fish Audio Agents (hosted real-time voice agents) integration.

Fish Agents runs the speech stack (ASR, turn-taking, TTS) and lets an agent act
through **client tools**: the tool is *declared* on the agent, but the handler
runs in the page that owns the session. Jarvis already owns that page and
already serves the matching local endpoints, so the three tools below are plain
client-tool declarations - no public URL, no webhook, no relay required.

    agent brain (Fish)  --client tool call-->  browser page (app.js)
                                                |
                                                +--> POST /api/live/execute
                                                +--> POST /api/interrupt
                                                +--> GET  /api/state
                                                |
                                        local Jarvis main worker agent

Tool names deliberately match ``jarvis.live.prompts.get_live_voice_tools()`` so
one set of handlers serves both the OpenAI-Realtime path and Fish Agents.

Private agents cannot be started from the browser with an ``agentId``: the
backend mints a session token with the API key and hands the response to the
SDK unchanged (see ``create_session`` and ``/api/voice/session`` in
``jarvis/browser.py``).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

#: Fish Audio API base. Override for a regional/proxied deployment.
DEFAULT_API_BASE = "https://api.fish.audio"

#: Fish tool names must match ^[a-zA-Z][a-zA-Z0-9_-]{0,63}$ and stay unique per agent.
MAX_TOOL_NAME = 64


class FishAPIError(RuntimeError):
    """A Fish Audio API call failed, carrying the upstream status.

    Subclasses RuntimeError so existing callers and messages keep working, but
    lets an HTTP layer tell "the account is out of credit" (402) apart from
    "the service is unreachable" - which need different words and different
    status codes on the way back to the browser.
    """

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status

    @property
    def code(self) -> str:
        """A short, machine-readable reason the UI can act on."""
        return {
            401: "unauthorized",
            402: "credit",
            403: "forbidden",
            404: "not_found",
            409: "conflict",
        }.get(self.status or 0, "upstream")

    @property
    def hint(self) -> str:
        """What the user should actually do about it."""
        if self.status == 402:
            return (
                "The Fish Audio account is out of API credit, so no voice session "
                "can be started. Top up at https://fish.audio/app/ , or point live "
                "voice at your own relay with live_voice.ws_url."
            )
        if self.status == 403:
            return (
                "Fish Agents is in private beta: access must be granted on the "
                "account (https://fish.audio/app/agents)."
            )
        return ""


def _api_base(explicit: str | None = None) -> str:
    base = (
        explicit
        or os.environ.get("JARVIS_FISH_API_BASE")
        or os.environ.get("FISH_AUDIO_API_BASE")
        or DEFAULT_API_BASE
    )
    return base.rstrip("/")


def resolve_api_key(config: Any | None = None) -> str:
    """Resolve the Fish Audio API key from config, environment, .env, or vault.

    Reuses the same resolution order as Fish Audio TTS so one key configures
    both the voice and the hosted agent.
    """
    from ..utils.voice import _get_fish_audio_key_dynamic

    key = _get_fish_audio_key_dynamic(config)
    if not key:
        raise RuntimeError(
            "Fish Audio API key is required. Set FISH_AUDIO_API_KEY in .env, "
            "config.yaml, or the Windows Credential Vault."
        )
    return key


def direct_tool_declarations() -> list[dict[str, Any]]:
    """Client-tool declarations for Jarvis's directly callable actions.

    Generated from the action schema, so the voice agent gets exactly the
    arguments the registry reads (see ``jarvis.live.direct_tools``). These are
    what let the voice agent *do* things in one hop instead of delegating every
    request to the perceive/think/act loop.
    """
    from . import direct_tools

    return direct_tools.declarations()


def control_tool_declarations() -> list[dict[str, Any]]:
    """The three control tools: delegate, cancel, and check on a task.

    Kept separate from the direct tools because they drive the main worker agent
    rather than executing anything themselves.
    """
    return [
        {
            "tool_type": "client",
            "name": "execute_task",
            "description": (
                "Prompt the Jarvis Main Worker Agent to autonomously execute a computer "
                "task or action on the Windows computer (e.g. launching applications, "
                "browsing websites, writing code, running terminal commands, manipulating "
                "files, clicking UI)."
            ),
            "arguments": [
                {
                    "name": "task",
                    "description": (
                        "The exact natural language task or directive for the Main Worker "
                        "Agent to execute."
                    ),
                }
            ],
            "expects_response": True,
        },
        {
            "tool_type": "client",
            "name": "cancel_task",
            "description": (
                "Cancel or interrupt the currently running computer task if the user asks "
                "to stop, cancel, or abort."
            ),
            "arguments": [
                {
                    "name": "reason",
                    "description": "Optional reason for cancelling the task.",
                }
            ],
            "expects_response": True,
        },
        {
            "tool_type": "client",
            "name": "get_task_status",
            "description": (
                "Query the current execution status, active action, and progress of the "
                "Jarvis Main Worker Agent."
            ),
            "arguments": [],
            "expects_response": True,
        },
    ]


def client_tool_declarations() -> list[dict[str, Any]]:
    """Every tool a Jarvis voice agent needs: control tools plus direct actions.

    Paste these into Builder -> Tools, or create them with ``provision_tools``.
    """
    return control_tool_declarations() + direct_tool_declarations()


def _request(
    method: str,
    url: str,
    api_key: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 20.0,
) -> Any:
    """Call the Fish Audio API and return the decoded JSON body."""
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Authorization": f"Bearer {api_key}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as err:
        detail = ""
        try:
            detail = err.read().decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover - best-effort error body
            pass
        if err.code == 403:
            raise FishAPIError(
                "Fish Agents returned 403 - Agents is in private beta and this account "
                "does not have access yet (https://fish.audio/app/agents).",
                status=403,
            ) from err
        raise FishAPIError(
            f"Fish Audio API error ({err.code}): {detail or err.reason}",
            status=err.code,
        ) from err
    except Exception as exc:
        raise FishAPIError(f"Fish Audio API request failed: {exc}") from exc
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Fish Audio returned non-JSON response: {raw[:200]}") from exc


#: The list endpoints are paginated and default to 30 rows. Jarvis declares far
#: more tools than that, so a single un-paged GET silently hides most of the
#: workspace - and a partial listing would otherwise attach a partial tool set.
PAGE_SIZE = 100
_MAX_PAGES = 100


def _page_items(data: Any, key: str) -> list[dict[str, Any]]:
    """Pull the row list out of a {"<key>": [...]} envelope."""
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for candidate in (key, "data", "items"):
            value = data.get(candidate)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _list_all(path: str, api_key: str, key: str, base_url: str | None = None) -> list[dict[str, Any]]:
    """Walk every page of a list endpoint via its ``next_cursor``."""
    from urllib.parse import urlencode

    base = _api_base(base_url)
    out: list[dict[str, Any]] = []
    cursor: str | None = None
    seen: set[str] = set()
    for _ in range(_MAX_PAGES):
        params: dict[str, Any] = {"page_size": PAGE_SIZE}
        if cursor:
            params["cursor"] = cursor
        data = _request("GET", f"{base}{path}?{urlencode(params)}", api_key)
        out.extend(_page_items(data, key))
        if not isinstance(data, dict) or not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
        # A server that repeats a cursor would otherwise loop forever here.
        if not cursor or cursor in seen:
            break
        seen.add(cursor)
    return out


def list_tools(api_key: str, base_url: str | None = None) -> list[dict[str, Any]]:
    """Return every custom tool in the workspace, across all pages."""
    return _list_all("/v1/agent/tools", api_key, "tools", base_url)


def provision_tools(
    api_key: str,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Create any missing Jarvis client tools in the workspace.

    Idempotent: existing tools are left untouched, so this is safe to re-run.
    """
    existing = {}
    for tool in list_tools(api_key, base_url):
        name = tool.get("name")
        if isinstance(name, str):
            existing[name] = tool

    created: list[str] = []
    skipped: list[str] = []
    for declaration in client_tool_declarations():
        name = declaration["name"]
        if name in existing:
            skipped.append(name)
            continue
        result = _request(
            "POST",
            f"{_api_base(base_url)}/v1/agent/tools",
            api_key,
            payload=declaration,
        )
        tool_id = _tool_id(result) if isinstance(result, dict) else None
        created.append(tool_id or name)

    return {
        "created": created,
        "existing": skipped,
        "tool_names": [d["name"] for d in client_tool_declarations()],
    }


def _tool_id(tool: dict[str, Any]) -> str | None:
    for key in ("id", "tool_id", "toolId"):
        value = tool.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def list_agents(api_key: str, base_url: str | None = None) -> list[dict[str, Any]]:
    """Return every agent in the workspace, across all pages."""
    return _list_all("/v1/agent/agents", api_key, "agents", base_url)


def get_agent(api_key: str, agent_id: str, base_url: str | None = None) -> dict[str, Any]:
    """Retrieve one agent, including its draft ``config`` when the API exposes it."""
    if not agent_id.strip():
        raise ValueError("agent_id is required")
    data = _request(
        "GET", f"{_api_base(base_url)}/v1/agent/agents/{agent_id.strip()}", api_key
    )
    if not isinstance(data, dict):
        raise RuntimeError("Fish Agents returned an unexpected agent response")
    return data


def attach_tools(
    api_key: str,
    agent_id: str,
    tool_ids: list[str],
    base_url: str | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    """Attach custom tools to an agent's draft configuration.

    ``tools.tool_ids`` replaces the attached set wholesale, and every id must
    already exist in the workspace (or the API answers 422). Attaching edits the
    *draft*: call :func:`publish_agent` afterwards or live sessions keep running
    the previous version.
    """
    if not agent_id.strip():
        raise ValueError("agent_id is required")
    payload = {"tools": {"enabled": bool(enabled), "tool_ids": list(tool_ids)}}
    return _request(
        "PATCH",
        f"{_api_base(base_url)}/v1/agent/agents/{agent_id.strip()}/config",
        api_key,
        payload=payload,
    )


def update_agent_prompt(
    api_key: str,
    agent_id: str,
    system_prompt: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Replace the agent's draft system prompt with Jarvis's own.

    The tools and the instructions that tell the model *when* to call them live
    in different places - this module and ``jarvis/live/prompts.py`` locally, the
    prompt on Fish's servers. Keeping the remote copy in sync is what stops the
    two from drifting; publish afterwards for it to reach live sessions.
    """
    from .prompts import build_live_voice_system_prompt

    if not agent_id.strip():
        raise ValueError("agent_id is required")
    text = (
        build_live_voice_system_prompt() if system_prompt is None else str(system_prompt)
    )
    if not text.strip():
        raise ValueError("system_prompt must not be empty")
    return _request(
        "PATCH",
        f"{_api_base(base_url)}/v1/agent/agents/{agent_id.strip()}/config",
        api_key,
        payload={"prompt": {"system_prompt": text}},
    )


def update_agent_llm(
    api_key: str,
    agent_id: str,
    model: str,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Choose the LLM that answers and decides when to call a tool.

    It is the single biggest lever on whether the agent *acts* or merely
    narrates: an action request that reaches a weak model comes back as "I am
    opening that right now" with no tool call behind it.
    """
    if not agent_id.strip():
        raise ValueError("agent_id is required")
    model = str(model or "").strip()
    if not model:
        raise ValueError("model is required")
    return _request(
        "PATCH",
        f"{_api_base(base_url)}/v1/agent/agents/{agent_id.strip()}/config",
        api_key,
        payload={"llm": {"model": model}},
    )


def publish_agent(
    api_key: str,
    agent_id: str,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Freeze the draft into an immutable published version.

    Required for configuration changes to reach live sessions: sessions always
    run the published version.
    """
    if not agent_id.strip():
        raise ValueError("agent_id is required")
    return _request(
        "POST",
        f"{_api_base(base_url)}/v1/agent/agents/{agent_id.strip()}/publish",
        api_key,
    )


def setup_agent(
    api_key: str,
    agent_id: str,
    base_url: str | None = None,
    publish: bool = True,
    sync_prompt: bool = True,
    llm_model: str | None = None,
) -> dict[str, Any]:
    """Make an agent able to drive Jarvis: create, attach, and publish its tools.

    One call turns a talk-only agent into one that can act on the desktop. Safe
    to re-run - tool creation is idempotent and attachment replaces the set.
    """
    provisioned = provision_tools(api_key, base_url)
    if sync_prompt:
        update_agent_prompt(api_key, agent_id, base_url=base_url)
    if llm_model:
        update_agent_llm(api_key, agent_id, llm_model, base_url=base_url)
    declarations = client_tool_declarations()
    wanted = [declaration["name"] for declaration in declarations]

    by_name: dict[str, str] = {}
    for tool in list_tools(api_key, base_url):
        name = tool.get("name")
        tool_id = _tool_id(tool)
        if isinstance(name, str) and tool_id:
            by_name[name] = tool_id

    missing = sorted(set(wanted) - set(by_name))
    if missing:
        raise RuntimeError(f"Fish Audio did not return ids for tools: {', '.join(missing)}")
    ids = [by_name[name] for name in wanted]

    attach_tools(api_key, agent_id, ids, base_url)
    result: dict[str, Any] = {
        **provisioned,
        "agent_id": agent_id,
        "attached": sorted(wanted),
        "tool_ids": ids,
        "prompt_synced": bool(sync_prompt),
        "llm_model": str(llm_model or ""),
        "published": False,
    }
    if publish:
        publish_agent(api_key, agent_id, base_url)
        result["published"] = True
    return result


def create_session(
    api_key: str,
    agent_id: str,
    base_url: str | None = None,
    **options: Any,
) -> dict[str, Any]:
    """Mint a session token for a private agent.

    The response is handed to `@fishaudio/agent-client` as ``sessionToken``
    unchanged, so the browser never sees the API key.
    """
    if not agent_id.strip():
        raise ValueError("agent_id is required to create a Fish Agents session")
    payload: dict[str, Any] = {"agent_id": agent_id.strip()}
    payload.update({key: value for key, value in options.items() if value is not None})
    data = _request(
        "POST",
        f"{_api_base(base_url)}/v1/agent/sessions",
        api_key,
        payload=payload,
    )
    if not isinstance(data, dict) or not data:
        raise RuntimeError("Fish Agents returned an empty session response")
    return data


def _main(argv: list[str] | None = None) -> int:
    """``python -m jarvis.live.fish_agents [agent_id]`` - provision and publish."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Give a Fish Agents voice agent the three Jarvis client tools."
    )
    parser.add_argument(
        "agent_id",
        nargs="?",
        default=None,
        help="Agent id; defaults to live_voice.fish_agent_id from config.yaml/.env.",
    )
    parser.add_argument(
        "--no-publish",
        action="store_true",
        help="Attach the tools to the draft without publishing a new version.",
    )
    parser.add_argument(
        "--keep-prompt",
        action="store_true",
        help="Leave the agent's system prompt alone instead of syncing Jarvis's.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List the workspace's agents and custom tools, then exit.",
    )
    parser.add_argument(
        "--llm",
        default=None,
        metavar="MODEL",
        help=(
            "Set the agent's LLM (default: live_voice.fish_llm_model). The model "
            "decides whether tools are actually called, so a *-lite variant is a "
            "common cause of the agent narrating an action instead of taking it."
        ),
    )
    args = parser.parse_args(argv)

    from ..config import load_config

    config = load_config()
    api_key = resolve_api_key(config)

    if args.list:
        for agent in list_agents(api_key):
            print(
                f"agent  {agent.get('agent_id')}  {agent.get('publication_state', '?'):5}  "
                f"{agent.get('name')}"
            )
        for tool in list_tools(api_key):
            print(f"tool   {_tool_id(tool)}  {tool.get('tool_type', '?'):7}  {tool.get('name')}")
        return 0

    agent_id = args.agent_id or getattr(config.live_voice, "fish_agent_id", "")
    if not agent_id:
        raise SystemExit(
            "No agent id. Pass one as an argument or set live_voice.fish_agent_id "
            "(JARVIS_FISH_AGENT_ID)."
        )

    llm_model = args.llm or getattr(config.live_voice, "fish_llm_model", "") or ""
    result = setup_agent(
        api_key,
        agent_id,
        publish=not args.no_publish,
        sync_prompt=not args.keep_prompt,
        llm_model=llm_model,
    )
    print(json.dumps(result, indent=2))
    if result["published"]:
        print(
            f"\n'{agent_id}' now has {len(result['attached'])} tools published"
            + (f", running {llm_model}." if llm_model else ".")
            + " Restart live mode for new sessions to pick them up."
        )
    else:
        print(
            "\nAttached to the draft only - publish in the Builder (or re-run without "
            "--no-publish) before live sessions can use the tools."
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - manual entry point
    raise SystemExit(_main())
