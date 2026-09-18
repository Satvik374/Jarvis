"""Tests for Fish Audio Agents integration (client tools, provisioning, sessions)."""

from __future__ import annotations

import json
import re
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from jarvis.config import Config, VoiceConfig
from jarvis.live import fish_agents
from jarvis.live.prompts import get_live_voice_tools


class MockHTTPResponse:
    def __init__(self, payload):
        self._data = payload.encode("utf-8")

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


def _respond(payload):
    return patch("urllib.request.urlopen", return_value=MockHTTPResponse(json.dumps(payload)))


def test_client_tool_names_match_live_voice_tools():
    """Declared client tools must mirror the OpenAI Realtime tool names."""
    declared = [tool["name"] for tool in fish_agents.client_tool_declarations()]
    expected = [tool["name"] for tool in get_live_voice_tools()]
    assert declared == expected


def test_client_tool_declarations_follow_fish_schema():
    """Every declaration satisfies Fish's documented client-tool schema."""
    for tool in fish_agents.client_tool_declarations():
        assert tool["tool_type"] == "client"
        assert re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,63}", tool["name"])
        assert isinstance(tool["description"], str) and tool["description"]
        assert isinstance(tool["arguments"], list)
        for argument in tool["arguments"]:
            assert set(argument) == {"name", "description"}
            assert isinstance(argument["name"], str) and argument["name"]
        assert isinstance(tool["expects_response"], bool)


def test_declared_tool_names_are_unique():
    names = [tool["name"] for tool in fish_agents.client_tool_declarations()]
    assert len(names) == len(set(names))


def declared_names():
    return [tool["name"] for tool in fish_agents.client_tool_declarations()]


def direct_names():
    return [tool["name"] for tool in fish_agents.direct_tool_declarations()]


def workspace(declarations, *, with_ids=True):
    """A fake Fish workspace listing for the given declarations."""
    tools = []
    for index, declaration in enumerate(declarations):
        tool = {"name": declaration["name"], "tool_type": "client"}
        if with_ids:
            tool["tool_id"] = f"id_{index}_{declaration['name']}"
        tools.append(tool)
    return {"tools": tools}


def test_provision_tools_creates_only_missing_tools():
    """Existing workspace tools are left alone; absent ones are created."""
    existing = workspace(fish_agents.control_tool_declarations())
    created_payloads = []

    def fake_request(method, url, api_key, payload=None, timeout=20.0):
        if method == "GET":
            return existing
        created_payloads.append(payload)
        return {"id": f"tool_new_{len(created_payloads)}", "name": payload["name"]}

    with patch.object(fish_agents, "_request", side_effect=fake_request):
        result = fish_agents.provision_tools("test-key")

    assert sorted(result["existing"]) == ["cancel_task", "execute_task", "get_task_status"]
    assert [payload["name"] for payload in created_payloads] == [
        tool["name"] for tool in fish_agents.direct_tool_declarations()
    ]
    assert all(payload["tool_type"] == "client" for payload in created_payloads)


def test_provision_tools_is_idempotent():
    """A second run creates nothing when every tool already exists."""
    existing = workspace(fish_agents.client_tool_declarations())
    calls = []

    def fake_request(method, url, api_key, payload=None, timeout=20.0):
        calls.append(method)
        return existing

    with patch.object(fish_agents, "_request", side_effect=fake_request):
        result = fish_agents.provision_tools("test-key")

    assert result["created"] == []
    assert sorted(result["existing"]) == sorted(declared_names())
    assert calls == ["GET"]


def test_create_session_posts_agent_id_and_returns_response_unchanged():
    """The backend hands the Fish session response to the browser untouched."""
    response = {
        "session_id": "sess_123",
        "token": "opaque-token",
        "expires_at": 4102444800,
    }
    captured = {}

    def fake_request(method, url, api_key, payload=None, timeout=20.0):
        captured.update(method=method, url=url, api_key=api_key, payload=payload)
        return response

    with patch.object(fish_agents, "_request", side_effect=fake_request):
        result = fish_agents.create_session("fish-key", "agent_abc", end_user_id="satvik")

    assert result == response
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/v1/agent/sessions")
    assert captured["api_key"] == "fish-key"
    assert captured["payload"] == {"agent_id": "agent_abc", "end_user_id": "satvik"}


def test_create_session_rejects_empty_agent_id():
    with pytest.raises(ValueError, match="agent_id"):
        fish_agents.create_session("fish-key", "   ")


@pytest.mark.parametrize("agent_id", ["", "   "])
def test_empty_agent_id_never_reaches_the_api(agent_id):
    with patch.object(fish_agents, "_request") as request:
        with pytest.raises(ValueError):
            fish_agents.create_session("fish-key", agent_id)
    request.assert_not_called()


def test_private_beta_403_error_is_explained():
    """Fish Agents returns 403 until beta access is granted; say so plainly."""
    error = urllib.error.HTTPError(
        "https://api.fish.audio/v1/agent/tools", 403, "Forbidden", {}, None
    )
    error.read = lambda: b'{"detail":"beta access required"}'
    with patch("urllib.request.urlopen", side_effect=error):
        with pytest.raises(RuntimeError, match="private beta"):
            fish_agents.list_tools("fish-key")


def test_api_error_surfaces_status_and_body():
    error = urllib.error.HTTPError(
        "https://api.fish.audio/v1/agent/sessions", 400, "Bad Request", {}, None
    )
    error.read = lambda: b'duplicate tool name'
    with patch("urllib.request.urlopen", side_effect=error):
        with pytest.raises(RuntimeError, match="400.*duplicate tool name"):
            fish_agents.create_session("fish-key", "agent_abc")


def test_non_json_response_is_reported():
    with patch("urllib.request.urlopen", return_value=MockHTTPResponse("<html>oops</html>")):
        with pytest.raises(RuntimeError, match="non-JSON"):
            fish_agents.list_tools("k")


def test_list_tools_accepts_wrapped_and_bare_payloads():
    with _respond({"tools": [{"id": "1", "name": "execute_task"}]}):
        assert fish_agents.list_tools("k") == [{"id": "1", "name": "execute_task"}]
    with _respond([{"id": "2", "name": "cancel_task"}]):
        assert fish_agents.list_tools("k") == [{"id": "2", "name": "cancel_task"}]
    with _respond({"data": [{"id": "3", "name": "get_task_status"}]}):
        assert fish_agents.list_tools("k") == [{"id": "3", "name": "get_task_status"}]


def test_resolve_api_key_uses_config_then_errors_clearly():
    assert fish_agents.resolve_api_key(VoiceConfig(fish_audio_key="cfg-fish-key")) == "cfg-fish-key"
    with patch.dict("os.environ", {}, clear=True), patch(
        "pathlib.Path.exists", return_value=False
    ), patch("jarvis.security.get_secret", return_value=""):
        with pytest.raises(RuntimeError, match="Fish Audio API key"):
            fish_agents.resolve_api_key(VoiceConfig())


def _handler_with_recorder():
    from jarvis.browser import BrowserRequestHandler

    handler = object.__new__(BrowserRequestHandler)
    handler._require_api_access = lambda require_origin=False: True
    responses: list = []
    handler._json = lambda status, body: responses.append((status, body))
    return handler, responses


def test_voice_session_endpoint_needs_a_configured_agent(monkeypatch):
    """Without an agent id the endpoint explains what to configure."""
    from http import HTTPStatus

    monkeypatch.delenv("JARVIS_FISH_AGENT_ID", raising=False)
    monkeypatch.delenv("FISH_AGENT_ID", raising=False)
    handler, responses = _handler_with_recorder()
    with patch("jarvis.config.load_config", return_value=Config()):
        handler._handle_voice_session({})

    assert len(responses) == 1
    assert responses[0][0] == HTTPStatus.BAD_REQUEST
    assert "fish_agent_id" in responses[0][1]["error"]


def test_voice_session_endpoint_mints_session_server_side(monkeypatch):
    """The API key stays server-side; the browser gets the session JSON back."""
    from http import HTTPStatus

    monkeypatch.delenv("JARVIS_FISH_AGENT_ID", raising=False)
    monkeypatch.delenv("FISH_AGENT_ID", raising=False)
    cfg = Config()
    cfg.live_voice.fish_agent_id = "agent_from_config"
    handler, responses = _handler_with_recorder()

    with patch("jarvis.config.load_config", return_value=cfg), \
         patch("jarvis.live.fish_agents.resolve_api_key", return_value="fish-key"), \
         patch(
             "jarvis.live.fish_agents.create_session",
             return_value={"token": "opaque"},
         ) as create:
        handler._handle_voice_session({})

    create.assert_called_once()
    assert create.call_args.args[:2] == ("fish-key", "agent_from_config")
    assert responses[0][0] == HTTPStatus.OK
    assert responses[0][1]["session"] == {"token": "opaque"}


def test_voice_session_endpoint_reports_upstream_failure(monkeypatch):
    from http import HTTPStatus

    monkeypatch.delenv("JARVIS_FISH_AGENT_ID", raising=False)
    cfg = Config()
    cfg.live_voice.fish_agent_id = "agent_x"
    handler, responses = _handler_with_recorder()

    with patch("jarvis.config.load_config", return_value=cfg), \
         patch("jarvis.live.fish_agents.resolve_api_key", return_value="fish-key"), \
         patch(
             "jarvis.live.fish_agents.create_session",
             side_effect=RuntimeError("Fish Agents returned 403"),
         ):
        handler._handle_voice_session({})

    assert responses[0][0] == HTTPStatus.BAD_GATEWAY
    assert "403" in responses[0][1]["error"]


def test_session_endpoint_passes_the_credit_status_through():
    """"Out of API credit" is not a bad gateway - the page acts on the difference."""
    from http import HTTPStatus

    cfg = Config()
    cfg.live_voice.fish_agent_id = "agent_x"
    handler, responses = _handler_with_recorder()
    error = fish_agents.FishAPIError(
        'Fish Audio API error (402): {"message":"Out of API credit"}', status=402
    )

    with patch("jarvis.config.load_config", return_value=cfg), \
         patch("jarvis.live.fish_agents.resolve_api_key", return_value="fish-key"), \
         patch("jarvis.live.fish_agents.create_session", side_effect=error):
        handler._handle_voice_session({})

    status, body = responses[0]
    assert status == HTTPStatus.PAYMENT_REQUIRED
    assert body["code"] == "credit"
    assert "credit" in body["hint"]


def test_session_endpoint_keeps_502_for_a_transport_failure():
    from http import HTTPStatus

    cfg = Config()
    cfg.live_voice.fish_agent_id = "agent_x"
    handler, responses = _handler_with_recorder()
    error = fish_agents.FishAPIError("Fish Audio API request failed: timed out")

    with patch("jarvis.config.load_config", return_value=cfg), \
         patch("jarvis.live.fish_agents.resolve_api_key", return_value="fish-key"), \
         patch("jarvis.live.fish_agents.create_session", side_effect=error):
        handler._handle_voice_session({})

    assert responses[0][0] == HTTPStatus.BAD_GATEWAY
    assert responses[0][1]["code"] == "upstream"


def test_fish_api_error_classification():
    assert fish_agents.FishAPIError("x", status=402).code == "credit"
    assert fish_agents.FishAPIError("x", status=403).code == "forbidden"
    assert fish_agents.FishAPIError("x").code == "upstream"
    # Still a RuntimeError, so every existing caller keeps working.
    assert isinstance(fish_agents.FishAPIError("x", status=402), RuntimeError)


def test_http_errors_carry_the_upstream_status():
    """_request must classify, not flatten, so the HTTP layer can too."""
    error = urllib.error.HTTPError(
        "https://api.fish.audio/v1/agent/sessions", 402, "Payment Required", {}, None
    )
    error.read = lambda: b'{"message":"Out of API credit"}'
    with patch("urllib.request.urlopen", side_effect=error):
        with pytest.raises(fish_agents.FishAPIError) as caught:
            fish_agents.create_session("fish-key", "agent_abc")
    assert caught.value.status == 402
    assert caught.value.code == "credit"


def test_vendored_bundle_is_served_by_the_loopback_server():
    """The SDK is vendored, allow-listed, and served same-origin (no CDN)."""
    from jarvis.browser import BrowserRequestHandler, STATIC_DIR

    vendor = STATIC_DIR / "vendor"
    assert (vendor / "fish-agent-client.esm.js").is_file()
    assert (vendor / "fish-agent-client.LICENSE").is_file()
    assert (vendor / "README.md").is_file()

    entry = BrowserRequestHandler._STATIC["/vendor/fish-agent-client.esm.js"]
    assert entry == (
        "vendor/fish-agent-client.esm.js",
        "text/javascript; charset=utf-8",
    )

    # The page must not reach out to a CDN for the SDK.
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert 'import("/vendor/fish-agent-client.esm.js")' in app_js


def test_vendored_bundle_exports_the_agent_session():
    """Guard against vendoring a file that is not a usable ES module."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    from jarvis.browser import STATIC_DIR

    vendor = STATIC_DIR / "vendor"
    check = (
        "import('./fish-agent-client.esm.js')"
        ".then(m => console.log(Object.keys(m).sort().join(',')))"
        ".catch(e => { console.error(String(e)); process.exit(1); })"
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", check],
        cwd=str(vendor),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    exports = result.stdout.strip().split(",")
    assert "AgentSession" in exports
    assert "FishAgentError" in exports


def test_live_config_reports_the_configured_fish_agent(monkeypatch):
    from http import HTTPStatus

    monkeypatch.delenv("JARVIS_FISH_AGENT_ID", raising=False)
    monkeypatch.delenv("FISH_AGENT_ID", raising=False)
    cfg = Config()
    cfg.live_voice.fish_agent_id = "agent_xyz"
    handler, responses = _handler_with_recorder()

    with patch("jarvis.config.load_config", return_value=cfg):
        handler._handle_live_config()

    assert responses[0][0] == HTTPStatus.OK
    payload = responses[0][1]
    assert payload["fish_agent_id"] == "agent_xyz"
    # The OpenAI-Realtime spelling of the same tools must stay available.
    names = [tool["name"] for tool in payload["tools"]]
    assert names[:3] == [
        "execute_task",
        "cancel_task",
        "get_task_status",
    ]
    # ... and the direct actions, so the page can register a handler per tool.
    assert payload["direct_tools"] == direct_names()
    for name in ("open_app", "run_command", "read_file", "system_status"):
        assert name in names and name in payload["direct_tools"]
    # The risky ones must never reach the page as callable tools.
    for name in ("click", "type", "self_upgrade", "secret"):
        assert name not in names and name not in payload["direct_tools"]
    assert payload["system_prompt"].startswith("You are JARVIS")


def test_live_config_omits_the_fish_agent_when_unset(monkeypatch):
    from http import HTTPStatus

    monkeypatch.delenv("JARVIS_FISH_AGENT_ID", raising=False)
    monkeypatch.delenv("FISH_AGENT_ID", raising=False)
    handler, responses = _handler_with_recorder()

    with patch("jarvis.config.load_config", return_value=Config()):
        handler._handle_live_config()

    assert responses[0][0] == HTTPStatus.OK
    assert responses[0][1]["fish_agent_id"] == ""


def test_csp_allows_the_fish_api_but_no_external_scripts():
    """The page may reach the Fish API for public-agent sessions, but the UI
    still refuses to load third-party scripts (the SDK is vendored)."""
    from jarvis.browser import BrowserRequestHandler

    handler = object.__new__(BrowserRequestHandler)
    headers = {}
    handler.send_header = lambda key, value: headers.__setitem__(key, value)
    handler._security_headers()

    csp = headers["Content-Security-Policy"]
    connect = [part for part in csp.split(";") if part.strip().startswith("connect-src")][0]
    script = [part for part in csp.split(";") if part.strip().startswith("script-src")][0]

    assert "https://api.fish.audio" in connect
    assert "wss:" in connect  # LiveKit transport
    assert "'self'" in script
    assert "unpkg.com" not in csp
    assert "http://" not in csp and "https://unpkg" not in script


def test_configured_fish_agent_id_is_loaded_from_config_yaml():
    """The owner's agent id must survive config loading (it drives live mode)."""
    from jarvis.config import load_config

    cfg = load_config()
    agent_id = cfg.live_voice.fish_agent_id.strip()
    assert agent_id, "live_voice.fish_agent_id is empty - live mode will use the realtime fallback"
    assert re.fullmatch(r"[0-9a-f]{32}", agent_id), agent_id


def test_api_base_override_from_environment(monkeypatch):
    monkeypatch.setenv("JARVIS_FISH_API_BASE", "https://relay.example.test/")
    captured = {}

    def fake_request(method, url, api_key, payload=None, timeout=20.0):
        captured["url"] = url
        return {"tools": []}

    with patch.object(fish_agents, "_request", side_effect=fake_request):
        fish_agents.list_tools("k")

    assert captured["url"].startswith("https://relay.example.test/v1/agent/tools?")


def test_list_tools_walks_every_page():
    """The API pages at 30 rows by default; an un-paged read hides most of the
    workspace, which would silently attach only some of the voice tools."""
    pages = [
        {"tools": [{"tool_id": "a", "name": "execute_task"}],
         "has_more": True, "next_cursor": "c1"},
        {"tools": [{"tool_id": "b", "name": "open_app"}],
         "has_more": True, "next_cursor": "c2"},
        {"tools": [{"tool_id": "c", "name": "read_file"}],
         "has_more": False, "next_cursor": None},
    ]
    urls = []

    def fake_request(method, url, api_key, payload=None, timeout=20.0):
        urls.append(url)
        return pages[len(urls) - 1]

    with patch.object(fish_agents, "_request", side_effect=fake_request):
        tools = fish_agents.list_tools("k")

    assert [tool["name"] for tool in tools] == ["execute_task", "open_app", "read_file"]
    assert len(urls) == 3
    assert "cursor=c1" in urls[1] and "cursor=c2" in urls[2]
    assert "page_size=" in urls[0]


def test_list_tools_stops_on_a_repeated_cursor():
    """A server echoing the same cursor must not spin forever."""
    calls = []

    def fake_request(method, url, api_key, payload=None, timeout=20.0):
        calls.append(url)
        return {"tools": [{"tool_id": "a", "name": "execute_task"}],
                "has_more": True, "next_cursor": "same"}

    with patch.object(fish_agents, "_request", side_effect=fake_request):
        tools = fish_agents.list_tools("k")

    # First page, then one follow-up with cursor=same, then the repeat stops it.
    assert len(calls) == 2
    assert len(tools) == 2
    assert len(calls) < fish_agents._MAX_PAGES


def test_list_agents_follows_pagination_too():
    def fake_request(method, url, api_key, payload=None, timeout=20.0):
        if "cursor=" in url:
            return {"agents": [{"agent_id": "b"}], "has_more": False}
        return {"agents": [{"agent_id": "a"}], "has_more": True, "next_cursor": "c1"}

    with patch.object(fish_agents, "_request", side_effect=fake_request):
        agents = fish_agents.list_agents("k")

    assert [agent["agent_id"] for agent in agents] == ["a", "b"]


def test_attach_tools_replaces_the_attached_set():
    """Attachment must hit the agent config endpoint with tools.tool_ids."""
    calls = []

    def fake_request(method, url, api_key, payload=None, timeout=20.0):
        calls.append((method, url, payload))
        return {}

    with patch.object(fish_agents, "_request", side_effect=fake_request):
        fish_agents.attach_tools("k", " agent_1 ", ["t1", "t2"])

    method, url, payload = calls[0]
    assert method == "PATCH"
    assert url == "https://api.fish.audio/v1/agent/agents/agent_1/config"
    assert payload == {"tools": {"enabled": True, "tool_ids": ["t1", "t2"]}}


def test_publish_agent_posts_to_publish():
    """Sessions only run published config, so publishing is not optional."""
    calls = []

    def fake_request(method, url, api_key, payload=None, timeout=20.0):
        calls.append((method, url))
        return {}

    with patch.object(fish_agents, "_request", side_effect=fake_request):
        fish_agents.publish_agent("k", "agent_1")

    assert calls == [("POST", "https://api.fish.audio/v1/agent/agents/agent_1/publish")]


def test_setup_agent_matches_ids_by_name_not_position():
    """The API lists tools in its own order; ids must be resolved by name."""
    listing = workspace(fish_agents.client_tool_declarations())
    listing["tools"].reverse()
    listing["tools"].append({"tool_id": "id_unrelated", "name": "lookup_order"})
    patches = []

    def fake_request(method, url, api_key, payload=None, timeout=20.0):
        if method == "GET":
            return listing
        patches.append((method, url, payload))
        return {}

    with patch.object(fish_agents, "_request", side_effect=fake_request):
        result = fish_agents.setup_agent("k", "agent_1", sync_prompt=False)

    expected = [
        f"id_{index}_{name}"
        for index, name in enumerate(declared_names())
    ]
    # Declaration order, not the API's listing order.
    assert result["tool_ids"] == expected
    assert "id_unrelated" not in result["tool_ids"]
    assert [method for method, _, _ in patches] == ["PATCH", "POST"]
    assert patches[0][2]["tools"]["tool_ids"] == result["tool_ids"]
    assert result["published"] is True


def test_setup_agent_creates_missing_tools_before_attaching():
    """A workspace with no tools gets them created, then attached, then published."""
    declarations = fish_agents.client_tool_declarations()
    state = {"tools": []}
    order = []

    def fake_request(method, url, api_key, payload=None, timeout=20.0):
        if method == "GET":
            return {"tools": list(state["tools"])}
        if url.endswith("/v1/agent/tools"):
            tool = {"tool_id": f"id_{payload['name']}", "name": payload["name"]}
            state["tools"].append(tool)
            order.append(f"create:{payload['name']}")
            return tool
        order.append(f"{'publish' if url.endswith('/publish') else 'attach'}")
        return {}

    with patch.object(fish_agents, "_request", side_effect=fake_request):
        result = fish_agents.setup_agent("k", "agent_1", sync_prompt=False)

    assert order == [
        *(f"create:{declaration['name']}" for declaration in declarations),
        "attach",
        "publish",
    ]
    assert result["created"] == [f"id_{name}" for name in declared_names()]


def test_setup_agent_can_skip_publishing():
    listing = workspace(fish_agents.client_tool_declarations())

    def fake_request(method, url, api_key, payload=None, timeout=20.0):
        if method == "GET":
            return listing
        assert not url.endswith("/publish")
        return {}

    with patch.object(fish_agents, "_request", side_effect=fake_request):
        result = fish_agents.setup_agent("k", "agent_1", publish=False, sync_prompt=False)

    assert result["published"] is False
    assert result["prompt_synced"] is False


def test_setup_agent_syncs_the_system_prompt_by_default():
    """The remote prompt is what teaches the model *when* to call its tools, so
    it has to be pushed alongside them or the two drift apart."""
    listing = workspace(fish_agents.client_tool_declarations())
    patches = []

    def fake_request(method, url, api_key, payload=None, timeout=20.0):
        if method == "GET":
            return listing
        patches.append((url, payload))
        return {}

    with patch.object(fish_agents, "_request", side_effect=fake_request):
        result = fish_agents.setup_agent("k", "agent_1")

    prompt_patch = [payload for url, payload in patches if "prompt" in (payload or {})]
    assert len(prompt_patch) == 1
    assert "execute_task" in prompt_patch[0]["prompt"]["system_prompt"]
    assert result["prompt_synced"] is True


def test_setup_agent_applies_the_configured_llm():
    """The model decides whether tools are called at all, so it is applied by
    the same one-command setup rather than left to drift in the console."""
    listing = workspace(fish_agents.client_tool_declarations())
    patches = []

    def fake_request(method, url, api_key, payload=None, timeout=20.0):
        if method == "GET":
            return listing
        patches.append(payload or {})
        return {}

    with patch.object(fish_agents, "_request", side_effect=fake_request):
        result = fish_agents.setup_agent(
            "k", "agent_1", sync_prompt=False, llm_model="google/gemini-3.6-flash"
        )

    llm_patch = [p for p in patches if "llm" in p]
    assert llm_patch == [{"llm": {"model": "google/gemini-3.6-flash"}}]
    assert result["llm_model"] == "google/gemini-3.6-flash"


def test_setup_agent_leaves_the_llm_alone_when_unspecified():
    listing = workspace(fish_agents.client_tool_declarations())
    patches = []

    def fake_request(method, url, api_key, payload=None, timeout=20.0):
        if method == "GET":
            return listing
        patches.append(payload or {})
        return {}

    with patch.object(fish_agents, "_request", side_effect=fake_request):
        fish_agents.setup_agent("k", "agent_1", sync_prompt=False)

    assert not [p for p in patches if "llm" in p]


def test_update_agent_llm_rejects_an_empty_model():
    with patch.object(fish_agents, "_request") as request:
        with pytest.raises(ValueError):
            fish_agents.update_agent_llm("k", "agent_1", "  ")
    request.assert_not_called()


def test_update_agent_prompt_rejects_an_empty_prompt():
    with patch.object(fish_agents, "_request") as request:
        with pytest.raises(ValueError):
            fish_agents.update_agent_prompt("k", "agent_1", "   ")
    request.assert_not_called()


def test_setup_agent_fails_loudly_when_an_id_is_missing():
    """Better to error than to silently attach a partial tool set."""
    writes = []

    def fake_request(method, url, api_key, payload=None, timeout=20.0):
        if method == "GET":
            # A tool that exists but reports no usable id cannot be attached.
            return {"tools": [{"name": "execute_task"}]}
        writes.append(url)
        return {"tool_id": f"id_{payload.get('name', 'prompt')}"}

    with patch.object(fish_agents, "_request", side_effect=fake_request):
        with pytest.raises(RuntimeError, match="did not return ids"):
            fish_agents.setup_agent("k", "agent_1", sync_prompt=False)

    # Only the tool creations happened: no attach, no publish.
    assert all(url.endswith("/v1/agent/tools") for url in writes)
    assert len(writes) == len(declared_names()) - 1


def test_get_agent_and_agent_id_validation():
    def fake_request(method, url, api_key, payload=None, timeout=20.0):
        assert url == "https://api.fish.audio/v1/agent/agents/agent_1"
        return {"agent_id": "agent_1", "name": "jarvis new"}

    with patch.object(fish_agents, "_request", side_effect=fake_request):
        assert fish_agents.get_agent("k", " agent_1 ")["name"] == "jarvis new"

    with pytest.raises(ValueError):
        fish_agents.get_agent("k", "   ")


def test_main_list_prints_agents_and_tools(monkeypatch, capsys):
    """`python -m jarvis.live.fish_agents --list` is the discovery command."""
    cfg = Config()
    cfg.live_voice.fish_agent_id = ""
    monkeypatch.setattr("jarvis.config.load_config", lambda *a, **k: cfg)
    monkeypatch.setattr(fish_agents, "resolve_api_key", lambda *a, **k: "k")
    monkeypatch.setattr(
        fish_agents,
        "list_agents",
        lambda *a, **k: [
            {"agent_id": "a1", "name": "jarvis new", "publication_state": "live"}
        ],
    )
    monkeypatch.setattr(
        fish_agents,
        "list_tools",
        lambda *a, **k: [{"tool_id": "t1", "name": "execute_task", "tool_type": "client"}],
    )

    assert fish_agents._main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "a1" in out and "jarvis new" in out
    assert "t1" in out and "execute_task" in out


def test_main_requires_an_agent_id(monkeypatch):
    cfg = Config()
    cfg.live_voice.fish_agent_id = ""
    monkeypatch.setattr("jarvis.config.load_config", lambda *a, **k: cfg)
    monkeypatch.setattr(fish_agents, "resolve_api_key", lambda *a, **k: "k")

    with pytest.raises(SystemExit):
        fish_agents._main([])
