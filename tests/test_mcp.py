"""MCP support: the stdio JSON-RPC client, the server manager, and the
mcp/mcp_call actions - exercised end to end against a real fake MCP server
subprocess (no `mcp` package, no network)."""

import sys
import tempfile
import unittest
from pathlib import Path

import jarvis.mcp as mcp
from jarvis.config import Config
from jarvis.perception.elements import Observation
from jarvis.tools import registry


# A minimal but protocol-correct MCP stdio server. It answers initialize,
# tools/list and tools/call, and emits a stray notification after the
# initialized handshake to prove the client skips non-matching messages.
_FAKE_SERVER = r'''
import sys, json
def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n"); sys.stdout.flush()
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except Exception:
        continue
    mid, method = msg.get("id"), msg.get("method")
    if method == "initialize":
        send({"jsonrpc":"2.0","id":mid,"result":{"protocolVersion":"2024-11-05",
              "capabilities":{"tools":{}},"serverInfo":{"name":"fake","version":"0.1"}}})
    elif method == "notifications/initialized":
        send({"jsonrpc":"2.0","method":"notifications/message",
              "params":{"level":"info","data":"ready"}})   # stray notification
    elif method == "tools/list":
        send({"jsonrpc":"2.0","id":mid,"result":{"tools":[{"name":"echo",
              "description":"Echo the text back","inputSchema":{"type":"object",
              "properties":{"text":{"type":"string"}},"required":["text"]}}]}})
    elif method == "tools/call":
        p = msg.get("params", {}); a = p.get("arguments", {})
        if p.get("name") == "echo":
            send({"jsonrpc":"2.0","id":mid,"result":{"content":[{"type":"text",
                  "text":a.get("text","")}],"isError":False}})
        else:
            send({"jsonrpc":"2.0","id":mid,"result":{"content":[{"type":"text",
                  "text":"no such tool"}],"isError":True}})
    elif mid is not None:
        send({"jsonrpc":"2.0","id":mid,"error":{"code":-32601,"message":"nope"}})
'''


class _FakeServerBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.script = self.tmp / "fake_server.py"
        self.script.write_text(_FAKE_SERVER, encoding="utf-8")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _manager(self):
        mgr = mcp.Manager(self.tmp / "mcp_servers.json")
        mgr.add("fake", sys.executable, [str(self.script)])
        return mgr


class MCPClientTests(_FakeServerBase):
    def test_connect_list_and_call(self):
        client = mcp.MCPClient(sys.executable, [str(self.script)]).connect()
        try:
            self.assertEqual(client.server_info.get("name"), "fake")
            tools = client.list_tools()
            self.assertEqual([t["name"] for t in tools], ["echo"])
            ok, text = client.call_tool("echo", {"text": "hi jarvis"})
            self.assertTrue(ok)
            self.assertEqual(text, "hi jarvis")
        finally:
            client.close()

    def test_tool_error_is_flagged(self):
        client = mcp.MCPClient(sys.executable, [str(self.script)]).connect()
        try:
            ok, text = client.call_tool("nope", {})
            self.assertFalse(ok)
            self.assertIn("no such tool", text)
        finally:
            client.close()

    def test_missing_command_raises(self):
        client = mcp.MCPClient("definitely-not-a-real-binary-xyz", [])
        with self.assertRaises(mcp.MCPError):
            client.connect(timeout=5)


class ManagerConfigTests(_FakeServerBase):
    def test_add_persists_in_claude_desktop_shape(self):
        import json
        mgr = self._manager()
        data = json.loads((self.tmp / "mcp_servers.json").read_text("utf-8"))
        self.assertIn("fake", data["mcpServers"])
        self.assertEqual(data["mcpServers"]["fake"]["command"], sys.executable)
        # a fresh manager reads the same file back
        self.assertIn("fake", mcp.Manager(self.tmp / "mcp_servers.json").servers())

    def test_enable_disable_and_remove(self):
        mgr = self._manager()
        self.assertEqual(mgr.enabled(), ["fake"])
        mgr.set_enabled("fake", False)
        self.assertEqual(mgr.enabled(), [])
        mgr.set_enabled("fake", True)
        self.assertEqual(mgr.enabled(), ["fake"])
        self.assertTrue(mgr.remove("fake"))
        self.assertFalse(mgr.remove("fake"))
        self.assertEqual(mgr.servers(), {})


class ManagerCallTests(_FakeServerBase):
    def test_call_connects_lazily_and_returns_text(self):
        mgr = self._manager()
        ok, text = mgr.call("fake", "echo", {"text": "roger"})
        self.assertTrue(ok)
        self.assertEqual(text, "roger")
        self.assertEqual([t["name"] for t in mgr.cached_tools("fake")], ["echo"])
        mgr.close_all()

    def test_call_unknown_server(self):
        mgr = self._manager()
        ok, text = mgr.call("ghost", "echo", {})
        self.assertFalse(ok)
        self.assertIn("no MCP server", text)

    def test_call_disabled_server_refused(self):
        mgr = self._manager()
        mgr.set_enabled("fake", False)
        ok, text = mgr.call("fake", "echo", {"text": "x"})
        self.assertFalse(ok)
        self.assertIn("disabled", text)
        mgr.close_all()


class ManageAndNoteTests(_FakeServerBase):
    def _use_temp_singleton(self, mgr):
        self.addCleanup(setattr, mcp, "_MANAGER", mcp._MANAGER)
        mcp._MANAGER = mgr

    def test_manage_list_add_tools(self):
        mgr = mcp.Manager(self.tmp / "mcp_servers.json")
        self._use_temp_singleton(mgr)
        self.assertIn("No MCP servers configured", mcp.manage({"op": "list"}))
        out = mcp.manage({"op": "add", "name": "fake",
                          "command": sys.executable, "args": [str(self.script)]})
        self.assertIn("Connected", out)
        self.assertIn("fake", mcp.manage({"op": "list"}))
        self.assertIn("echo", mcp.manage({"op": "tools", "name": "fake"}))
        mgr.close_all()

    def test_tools_note_lists_connected_tools(self):
        mgr = self._manager()
        self._use_temp_singleton(mgr)
        mgr.connect("fake")                     # populate the tool cache
        note = mcp.tools_note()
        self.assertIn("MCP TOOLS", note)
        self.assertIn("fake.echo(text)", note)
        mgr.close_all()

    def test_tools_note_empty_without_servers(self):
        self._use_temp_singleton(mcp.Manager(self.tmp / "mcp_servers.json"))
        self.assertEqual(mcp.tools_note(), "")


class RegistryActionTests(_FakeServerBase):
    def setUp(self):
        super().setUp()
        self.cfg = Config()
        self.obs = Observation(elements=[], screen_size=(1920, 1080))
        self.addCleanup(setattr, mcp, "_MANAGER", mcp._MANAGER)
        self.mgr = self._manager()
        mcp._MANAGER = self.mgr
        self.addCleanup(self.mgr.close_all)

    def test_mcp_call_action_round_trips(self):
        r = registry.execute("mcp_call", {"server": "fake", "tool": "echo",
                             "arguments": {"text": "via action"}},
                             self.obs, self.cfg)
        self.assertTrue(r.ok)
        self.assertEqual(r.message, "via action")

    def test_mcp_call_needs_server_and_tool(self):
        r = registry.execute("mcp_call", {"tool": "echo"}, self.obs, self.cfg)
        self.assertFalse(r.ok)
        self.assertIn("needs", r.message)

    def test_mcp_action_lists_servers(self):
        r = registry.execute("mcp", {"op": "list"}, self.obs, self.cfg)
        self.assertTrue(r.ok)
        self.assertIn("fake", r.message)


if __name__ == "__main__":
    unittest.main()
