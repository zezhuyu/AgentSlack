"""Local protocol tests for the opt-in Engram MCP bridge; no live credentials."""
import json
import os
import threading
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agent_slack.engram_memory import EngramMemoryBridge
from agent_slack.mcp_server import _tool_definitions
from agent_slack.mcp_server import AgentSlackMCP


class EngramBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calls = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length))
                EngramBridgeTests.calls.append((payload, self.headers.get("Authorization")))
                if payload.get("method") == "initialize":
                    body = {"jsonrpc": "2.0", "id": payload["id"], "result": {"protocolVersion": "2025-03-26"}}
                elif payload.get("method") == "tools/call":
                    result = {"written": True, "tool": payload["params"]["name"], "args": payload["params"]["arguments"]}
                    body = {"jsonrpc": "2.0", "id": payload["id"], "result": {"structuredContent": {"result": result}}}
                else:
                    self.send_response(202)
                    self.end_headers()
                    return
                encoded = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, *_args):
                pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.endpoint = f"http://127.0.0.1:{cls.server.server_port}/mcp"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=2)
        cls.server.server_close()

    def test_configured_scope_write_gate_and_protocol_call(self):
        bridge = EngramMemoryBridge(self.endpoint, {"project": {
            "tokenEnv": "TEST_ENGRAM_TOKEN", "allowWrites": True,
            "scope": {"type": "workspace", "workspaceId": "fixed-workspace"},
            "collectionId": "fixed-collection",
        }})
        os.environ["TEST_ENGRAM_TOKEN"] = "t" * 40
        try:
            result = bridge.write("project", "engram_create", {"kind": "decision", "idempotencyKey": "once"})
            found = bridge.call("project", "engram_search", {"query": "deployment", "limit": 5})
            retrieved = bridge.call("project", "engram_get", {"objectId": "mem-1"})
            patched = bridge.write("project", "engram_patch", {
                "objectId": "mem-1", "expectedRevisionId": "rev-1", "idempotencyKey": "patch-once", "body": "Updated"})
        finally:
            os.environ.pop("TEST_ENGRAM_TOKEN", None)
        self.assertTrue(result["written"])
        self.assertTrue(found["written"])
        self.assertTrue(retrieved["written"])
        self.assertTrue(patched["written"])
        calls = [payload for payload, _auth in self.calls if payload.get("method") == "tools/call"]
        args = calls[0]["params"]["arguments"]
        self.assertEqual(args["scope"], {"type": "workspace", "workspaceId": "fixed-workspace", "collectionId": "fixed-collection"})
        self.assertEqual([item["params"]["name"] for item in calls], ["engram_create", "engram_search", "engram_get", "engram_patch"])
        self.assertEqual({auth for _payload, auth in self.calls}, {"Bearer " + "t" * 40})

    def test_unconfigured_workspace_and_disabled_write_are_rejected(self):
        bridge = EngramMemoryBridge(self.endpoint, {"project": {
            "tokenEnv": "TEST_ENGRAM_TOKEN", "allowWrites": False,
            "scope": {"type": "workspace", "workspaceId": "fixed-workspace"},
        }})
        os.environ["TEST_ENGRAM_TOKEN"] = "t" * 40
        try:
            with self.assertRaises(PermissionError):
                bridge.write("project", "engram_create", {})
            with self.assertRaises(PermissionError):
                bridge.call("other", "engram_search", {"query": "secret"})
        finally:
            os.environ.pop("TEST_ENGRAM_TOKEN", None)

    def test_public_plain_http_is_refused(self):
        with self.assertRaises(ValueError):
            EngramMemoryBridge("http://example.com/mcp", {})

    def test_agent_slack_mcp_exposes_only_fixed_scope_engram_operations(self):
        tools = {tool["name"]: tool for tool in _tool_definitions()}
        names = {"search_engram_memory", "get_engram_memory", "create_engram_memory", "patch_engram_memory", "deliver_engram_memory"}
        self.assertTrue(names <= tools.keys())
        self.assertNotIn("workspace_id", tools["create_engram_memory"]["inputSchema"]["properties"])
        self.assertEqual(tools["patch_engram_memory"]["inputSchema"]["required"], [
            "workspace_key", "object_id", "expected_revision_id", "idempotency_key"
        ])

    def test_delivery_checks_allowlisted_opted_in_direct_chat_and_deduplicates(self):
        config = {"scope": {"type": "workspace", "workspaceId": "fixed"}, "tokenEnv": "TEST_ENGRAM_TOKEN",
                  "allowDelivery": True, "deliveryChats": [{"chatId": "direct-1", "enabled": True, "userOptIn": True}]}
        class Storage:
            def __init__(self): self.messages = []
            def append_message(self, chat_id, message):
                message = {**message, "message_id": f"message-{len(self.messages) + 1}", "created_at": "2026-10-09T12:00:00+00:00"}
                self.messages.append(message)
                return {"messages": self.messages}
        class App:
            _chat_interaction_lock = threading.RLock()
            def __init__(self): self.storage = Storage()
            def get_chat(self, chat_id):
                return {"chat_id": chat_id, "kind": "direct", "messages": self.storage.messages} if chat_id == "direct-1" else None
        app = App()
        class Manager:
            def app_for_workspace(self, _workspace): return app
        class Bridge:
            def _config(self, _workspace): return config, "x" * 40
            def call(self, _workspace, tool, _args):
                assert tool == "engram_get"
                return {"id": "mem-1", "revisionId": "rev-2", "title": "Finding", "kind": "note",
                        "body": "A checked project finding", "uri": "mdb://workspaces/fixed/objects/mem-1",
                        "updatedAt": "2026-10-09T11:00:00Z"}
        adapter = AgentSlackMCP.__new__(AgentSlackMCP)
        adapter.manager, adapter.engram = Manager(), Bridge()
        sent = adapter.deliver_engram_memory("project", "mem-1", "direct-1", "delivery-1")
        duplicate = adapter.deliver_engram_memory("project", "mem-1", "direct-1", "delivery-1")
        self.assertEqual(sent["state"], "accepted")
        self.assertFalse(sent["duplicate"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(len(app.storage.messages), 1)
        self.assertEqual(app.storage.messages[0]["author_type"], "agent")
        self.assertIn("rev-2", app.storage.messages[0]["body"])
        with self.assertRaises(PermissionError):
            adapter.deliver_engram_memory("project", "mem-1", "other-chat", "delivery-2")


if __name__ == "__main__":
    unittest.main()
